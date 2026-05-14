namespace TokenScope.Core.Pricing;

/// <summary>
/// Watches a pricing.json file and atomically swaps the in-memory pricing table
/// when the file changes. A failed reload (validation or I/O error) keeps the
/// previously-loaded table in place and reports the failure via
/// <paramref name="onReload"/>.
/// </summary>
public sealed class PricingTableProvider : IPricingTable, IDisposable
{
    private readonly string _filePath;
    private readonly TimeProvider _clock;
    private readonly Action<PricingReloadEvent>? _onReload;
    private readonly TimeSpan _debounceInterval;
    private readonly FileSystemWatcher? _watcher;
    private readonly Timer? _debounceTimer;
    private readonly object _reloadGate = new();

    private PricingTable _current;
    private int _disposed;

    public PricingTableProvider(
        string filePath,
        bool watch = true,
        TimeSpan? debounceInterval = null,
        TimeProvider? clock = null,
        Action<PricingReloadEvent>? onReload = null)
    {
        _filePath = Path.GetFullPath(filePath);
        _clock = clock ?? TimeProvider.System;
        _onReload = onReload;
        _debounceInterval = debounceInterval ?? TimeSpan.FromMilliseconds(250);

        _current = PricingLoader.LoadFromFile(_filePath, _clock);
        _onReload?.Invoke(new PricingReloadEvent.Loaded(_current, _clock.GetUtcNow()));

        if (!watch)
        {
            return;
        }

        var dir = Path.GetDirectoryName(_filePath)
                  ?? throw new ArgumentException(
                      $"Cannot derive directory from path '{filePath}'.",
                      nameof(filePath));
        var name = Path.GetFileName(_filePath);

        _debounceTimer = new Timer(OnDebounceFired, state: null, Timeout.Infinite, Timeout.Infinite);
        _watcher = new FileSystemWatcher(dir, name)
        {
            NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size,
            EnableRaisingEvents = true,
        };
        _watcher.Changed += OnFileEvent;
        _watcher.Created += OnFileEvent;
        _watcher.Renamed += OnFileEvent;
    }

    public PricingTable Current => Volatile.Read(ref _current);

    public PricingLookupResult Lookup(string modelId, DateTimeOffset at) =>
        Current.Lookup(modelId, at);

    /// <summary>
    /// Re-reads <c>pricing.json</c> and atomically swaps the in-memory table on success.
    /// On failure the previously-loaded table is retained and the error is surfaced via
    /// the <c>onReload</c> callback.
    /// </summary>
    public void Reload()
    {
        if (Volatile.Read(ref _disposed) != 0)
        {
            return;
        }

        lock (_reloadGate)
        {
            try
            {
                var next = PricingLoader.LoadFromFile(_filePath, _clock);
                Volatile.Write(ref _current, next);
                _onReload?.Invoke(new PricingReloadEvent.Loaded(next, _clock.GetUtcNow()));
            }
            catch (PricingValidationException ex)
            {
                _onReload?.Invoke(new PricingReloadEvent.ValidationFailed(ex.Errors, _clock.GetUtcNow()));
            }
            catch (IOException ex)
            {
                _onReload?.Invoke(new PricingReloadEvent.IoFailed(ex.Message, _clock.GetUtcNow()));
            }
            catch (UnauthorizedAccessException ex)
            {
                _onReload?.Invoke(new PricingReloadEvent.IoFailed(ex.Message, _clock.GetUtcNow()));
            }
        }
    }

    private void OnFileEvent(object sender, FileSystemEventArgs e)
    {
        _debounceTimer?.Change(_debounceInterval, Timeout.InfiniteTimeSpan);
    }

    private void OnDebounceFired(object? state) => Reload();

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }

        if (_watcher is not null)
        {
            _watcher.EnableRaisingEvents = false;
            _watcher.Changed -= OnFileEvent;
            _watcher.Created -= OnFileEvent;
            _watcher.Renamed -= OnFileEvent;
            _watcher.Dispose();
        }

        _debounceTimer?.Dispose();
    }
}
