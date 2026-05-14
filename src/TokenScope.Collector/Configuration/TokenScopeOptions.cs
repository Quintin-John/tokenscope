using Microsoft.Extensions.Configuration;

namespace TokenScope.Collector.Configuration;

/// <summary>
/// Strongly-typed shape of <c>tokenscope.yaml</c>. The
/// <see cref="ConfigurationKeyNameAttribute"/> attributes map YAML
/// snake_case keys to C# PascalCase properties — Microsoft.Extensions.Configuration
/// does not normalise casing on its own.
/// </summary>
public sealed record TokenScopeOptions
{
    [ConfigurationKeyName("schema_version")]
    public int SchemaVersion { get; init; } = 1;

    [ConfigurationKeyName("otlp")]
    public OtlpOptions Otlp { get; init; } = new();

    [ConfigurationKeyName("session_logs")]
    public SessionLogsOptions SessionLogs { get; init; } = new();

    [ConfigurationKeyName("pricing")]
    public PricingOptions Pricing { get; init; } = new();

    [ConfigurationKeyName("state")]
    public StateOptions State { get; init; } = new();

    [ConfigurationKeyName("subscription_mode")]
    public string SubscriptionMode { get; init; } = "enterprise";

    [ConfigurationKeyName("logging")]
    public LoggingOptions Logging { get; init; } = new();
}

public sealed record OtlpOptions
{
    [ConfigurationKeyName("endpoint")]
    public string Endpoint { get; init; } = "http://localhost:4317";

    [ConfigurationKeyName("protocol")]
    public string Protocol { get; init; } = "grpc";
}

public sealed record SessionLogsOptions
{
    /// <summary>null = auto-detect ~/.claude/projects (permissive: warn + watch).
    /// Explicit value = strict: fail-fast if directory missing.</summary>
    [ConfigurationKeyName("path")]
    public string? Path { get; init; }

    [ConfigurationKeyName("initial_scan_enabled")]
    public bool InitialScanEnabled { get; init; } = true;

    /// <summary>null = no age limit. Positive integer = ignore files older than N days.</summary>
    [ConfigurationKeyName("initial_scan_max_age_days")]
    public int? InitialScanMaxAgeDays { get; init; } = 30;

    [ConfigurationKeyName("active_session_window_minutes")]
    public int ActiveSessionWindowMinutes { get; init; } = 10;
}

public sealed record PricingOptions
{
    [ConfigurationKeyName("config_path")]
    public string ConfigPath { get; init; } = "./config/pricing.json";

    [ConfigurationKeyName("hot_reload_enabled")]
    public bool HotReloadEnabled { get; init; } = true;
}

public sealed record StateOptions
{
    /// <summary>null = ~/.tokenscope/state. Resolved at startup; the dir is
    /// created on demand.</summary>
    [ConfigurationKeyName("path")]
    public string? Path { get; init; }
}

public sealed record LoggingOptions
{
    [ConfigurationKeyName("level")]
    public string Level { get; init; } = "Information";

    [ConfigurationKeyName("format")]
    public string Format { get; init; } = "console";
}
