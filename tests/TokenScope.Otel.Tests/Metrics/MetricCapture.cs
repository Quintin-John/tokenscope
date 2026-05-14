using System.Diagnostics.Metrics;
using TokenScope.Otel.Metrics;

namespace TokenScope.Otel.Tests.Metrics;

/// <summary>
/// In-memory capture of metric measurements emitted by the tokenscope meter.
/// Uses <see cref="MeterListener"/> from the BCL — no OTEL SDK dependency
/// required in tests. Observable instruments are sampled by calling
/// <see cref="SampleObservables"/>.
/// </summary>
internal sealed class MetricCapture : IDisposable
{
    public sealed record Sample(string Name, object Value, IReadOnlyList<KeyValuePair<string, object?>> Tags);

    private readonly MeterListener _listener;
    private readonly List<Sample> _samples = new();
    private readonly object _gate = new();

    public IReadOnlyList<Sample> Samples
    {
        get
        {
            lock (_gate)
            {
                return _samples.ToList();
            }
        }
    }

    public MetricCapture()
    {
        _listener = new MeterListener
        {
            InstrumentPublished = (instrument, listener) =>
            {
                if (instrument.Meter.Name == TokenScopeMetrics.MeterName)
                {
                    listener.EnableMeasurementEvents(instrument);
                }
            },
        };
        _listener.SetMeasurementEventCallback<long>(Record<long>);
        _listener.SetMeasurementEventCallback<double>(Record<double>);
        _listener.Start();
    }

    public void SampleObservables()
    {
        _listener.RecordObservableInstruments();
    }

    public IEnumerable<Sample> ByName(string name) =>
        Samples.Where(s => s.Name == name);

    public void Dispose() => _listener.Dispose();

    private void Record<T>(Instrument instrument, T measurement, ReadOnlySpan<KeyValuePair<string, object?>> tags, object? _)
        where T : struct
    {
        var sample = new Sample(instrument.Name, measurement!, tags.ToArray());
        lock (_gate)
        {
            _samples.Add(sample);
        }
    }
}
