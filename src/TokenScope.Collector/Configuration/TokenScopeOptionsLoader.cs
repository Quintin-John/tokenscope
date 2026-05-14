using System.Collections.Immutable;
using Microsoft.Extensions.Configuration;
using NetEscapades.Configuration.Yaml;

namespace TokenScope.Collector.Configuration;

public static class TokenScopeOptionsLoader
{
    public const int SupportedSchemaVersion = 1;

    private static readonly HashSet<string> AllowedProtocols = new(StringComparer.OrdinalIgnoreCase)
    {
        "grpc", "http",
    };

    private static readonly HashSet<string> AllowedSubscriptionModes = new(StringComparer.Ordinal)
    {
        "enterprise", "pro", "max5x", "max20x",
    };

    private static readonly HashSet<string> AllowedLogLevels = new(StringComparer.OrdinalIgnoreCase)
    {
        "Trace", "Debug", "Information", "Warning", "Error", "Critical", "None",
    };

    private static readonly HashSet<string> AllowedLogFormats = new(StringComparer.OrdinalIgnoreCase)
    {
        "console",
    };

    /// <summary>
    /// Load tokenscope.yaml from the given path, bind to
    /// <see cref="TokenScopeOptions"/>, run strict key + value validation,
    /// and resolve null path fields to platform defaults.
    /// </summary>
    public static ResolvedTokenScopeOptions LoadFromFile(string yamlPath, string? homeOverride = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(yamlPath);

        var config = new ConfigurationBuilder()
            .AddYamlFile(yamlPath, optional: false, reloadOnChange: false)
            .Build();

        return LoadFromConfiguration(config, homeOverride);
    }

    /// <summary>
    /// Internal entry point used by tests and by file-based loading. The
    /// caller hands in an already-built <see cref="IConfiguration"/> so
    /// tests can drive the loader from in-memory YAML strings or
    /// <see cref="ConfigurationBuilder.AddInMemoryCollection"/> sources.
    /// </summary>
    public static ResolvedTokenScopeOptions LoadFromConfiguration(IConfiguration config, string? homeOverride = null)
    {
        ArgumentNullException.ThrowIfNull(config);

        var errors = ImmutableArray.CreateBuilder<string>();

        // 1. Strict unknown-key check first — typos shadow real validation.
        foreach (var unknown in StrictKeyValidator.FindUnknownKeys<TokenScopeOptions>(config))
        {
            errors.Add($"Unknown configuration key '{unknown}'.");
        }

        // 2. Bind to typed options. Default values from the record apply for
        //    keys that aren't set.
        TokenScopeOptions options;
        try
        {
            options = config.Get<TokenScopeOptions>() ?? new TokenScopeOptions();
        }
        catch (InvalidOperationException ex)
        {
            errors.Add($"Failed to bind configuration: {ex.Message}");
            // Cannot proceed with value validation if binding failed.
            throw new TokenScopeOptionsValidationException(errors.ToImmutable());
        }

        // 3. Value validation.
        if (options.SchemaVersion != SupportedSchemaVersion)
        {
            errors.Add($"schema_version {options.SchemaVersion} is not supported (expected {SupportedSchemaVersion}).");
        }

        if (string.IsNullOrWhiteSpace(options.Otlp.Endpoint))
        {
            errors.Add("otlp.endpoint must not be empty.");
        }
        else if (!Uri.TryCreate(options.Otlp.Endpoint, UriKind.Absolute, out _))
        {
            errors.Add($"otlp.endpoint '{options.Otlp.Endpoint}' is not a valid absolute URI.");
        }

        if (!AllowedProtocols.Contains(options.Otlp.Protocol))
        {
            errors.Add($"otlp.protocol '{options.Otlp.Protocol}' is not allowed (must be one of: grpc, http).");
        }

        if (options.SessionLogs.InitialScanMaxAgeDays is { } maxAge && maxAge <= 0)
        {
            errors.Add($"session_logs.initial_scan_max_age_days {maxAge} must be positive or null.");
        }

        if (options.SessionLogs.ActiveSessionWindowMinutes <= 0)
        {
            errors.Add($"session_logs.active_session_window_minutes {options.SessionLogs.ActiveSessionWindowMinutes} must be positive.");
        }

        if (options.SessionLogs.PollingIntervalSeconds <= 0)
        {
            errors.Add($"session_logs.polling_interval_seconds {options.SessionLogs.PollingIntervalSeconds} must be positive.");
        }

        if (string.IsNullOrWhiteSpace(options.Pricing.ConfigPath))
        {
            errors.Add("pricing.config_path must not be empty.");
        }

        if (!AllowedSubscriptionModes.Contains(options.SubscriptionMode))
        {
            errors.Add($"subscription_mode '{options.SubscriptionMode}' is not allowed (must be one of: enterprise, pro, max5x, max20x).");
        }

        if (!AllowedLogLevels.Contains(options.Logging.Level))
        {
            errors.Add($"logging.level '{options.Logging.Level}' is not allowed (must be one of: Trace, Debug, Information, Warning, Error, Critical, None).");
        }

        if (!AllowedLogFormats.Contains(options.Logging.Format))
        {
            errors.Add($"logging.format '{options.Logging.Format}' is not allowed at v1 (must be: console).");
        }

        // 4. Path resolution.
        //    null = auto-detect (permissive). Explicit = strict (fail-fast if missing).
        var home = homeOverride ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

        string sessionLogsPath;
        bool sessionLogsPathIsExplicit;
        if (string.IsNullOrEmpty(options.SessionLogs.Path))
        {
            sessionLogsPath = Path.Combine(home, ".claude", "projects");
            sessionLogsPathIsExplicit = false;
        }
        else
        {
            sessionLogsPath = options.SessionLogs.Path;
            sessionLogsPathIsExplicit = true;
            if (!Directory.Exists(sessionLogsPath))
            {
                errors.Add($"session_logs.path '{sessionLogsPath}' does not exist (explicit paths are strict).");
            }
        }

        var statePath = string.IsNullOrEmpty(options.State.Path)
            ? Path.Combine(home, ".tokenscope", "state")
            : options.State.Path;

        var pricingPath = options.Pricing.ConfigPath;

        if (errors.Count > 0)
        {
            throw new TokenScopeOptionsValidationException(errors.ToImmutable());
        }

        return new ResolvedTokenScopeOptions(
            Options: options,
            SessionLogsPath: sessionLogsPath,
            SessionLogsPathIsExplicit: sessionLogsPathIsExplicit,
            StatePath: statePath,
            PricingConfigPath: pricingPath);
    }
}

/// <summary>
/// <see cref="TokenScopeOptions"/> together with resolved (non-null) absolute
/// paths. The collector consumes this rather than the raw options so every
/// downstream component sees an unambiguous path.
/// </summary>
public sealed record ResolvedTokenScopeOptions(
    TokenScopeOptions Options,
    string SessionLogsPath,
    bool SessionLogsPathIsExplicit,
    string StatePath,
    string PricingConfigPath);
