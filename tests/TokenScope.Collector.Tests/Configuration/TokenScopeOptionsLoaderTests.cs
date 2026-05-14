using System.Text;
using AwesomeAssertions;
using Microsoft.Extensions.Configuration;
using NetEscapades.Configuration.Yaml;
using TokenScope.Collector.Configuration;
using Xunit;

namespace TokenScope.Collector.Tests.Configuration;

public class TokenScopeOptionsLoaderTests : IDisposable
{
    private readonly string _tempDir;

    public TokenScopeOptionsLoaderTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "tokenscope-config-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { }
    }

    [Fact]
    public void Valid_FullConfig_LoadsAndResolvesDefaults()
    {
        var config = BuildConfig("""
            schema_version: 1
            otlp:
              endpoint: "http://localhost:4317"
              protocol: "grpc"
            session_logs:
              path: null
              initial_scan_enabled: true
              initial_scan_max_age_days: 30
              active_session_window_minutes: 10
            pricing:
              config_path: "./config/pricing.json"
              hot_reload_enabled: true
            state:
              path: null
            subscription_mode: "enterprise"
            logging:
              level: "Information"
              format: "console"
            """);

        var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        resolved.Options.Otlp.Endpoint.Should().Be("http://localhost:4317");
        resolved.Options.SessionLogs.InitialScanMaxAgeDays.Should().Be(30);
        resolved.SessionLogsPath.Should().Be(Path.Combine(_tempDir, ".claude", "projects"));
        resolved.SessionLogsPathIsExplicit.Should().BeFalse();
        resolved.StatePath.Should().Be(Path.Combine(_tempDir, ".tokenscope", "state"));
    }

    [Fact]
    public void UnknownKey_Rejected_WithFullPath()
    {
        var config = BuildConfig("""
            schema_version: 1
            session_logs:
              path: null
              scan_recursive: true
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("'session_logs.scan_recursive'"));
    }

    [Fact]
    public void UnknownTopLevelKey_Rejected()
    {
        var config = BuildConfig("""
            schema_version: 1
            uknown_top_level: 42
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("'uknown_top_level'"));
    }

    [Fact]
    public void WrongSchemaVersion_Rejected()
    {
        var config = BuildConfig("""
            schema_version: 999
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("schema_version 999"));
    }

    [Fact]
    public void InvalidProtocol_Rejected()
    {
        var config = BuildConfig("""
            schema_version: 1
            otlp:
              endpoint: "http://localhost:4317"
              protocol: "thrift"
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("otlp.protocol 'thrift'"));
    }

    [Fact]
    public void InvalidSubscriptionMode_Rejected()
    {
        var config = BuildConfig("""
            schema_version: 1
            subscription_mode: "team"
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("subscription_mode 'team'"));
    }

    [Fact]
    public void NegativeAgeDays_Rejected()
    {
        var config = BuildConfig("""
            schema_version: 1
            session_logs:
              initial_scan_max_age_days: -3
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("initial_scan_max_age_days"));
    }

    [Fact]
    public void ExplicitPath_MissingDirectory_FailsFast()
    {
        var missing = Path.Combine(_tempDir, "no-such-dir");
        var config = BuildConfig($"""
            schema_version: 1
            session_logs:
              path: "{missing.Replace("\\", "/")}"
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("does not exist"));
    }

    [Fact]
    public void ExplicitPath_Exists_Resolves()
    {
        var existing = Path.Combine(_tempDir, "logs");
        Directory.CreateDirectory(existing);
        var config = BuildConfig($"""
            schema_version: 1
            session_logs:
              path: "{existing.Replace("\\", "/")}"
            """);

        var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        resolved.SessionLogsPath.Should().Be(existing);
        resolved.SessionLogsPathIsExplicit.Should().BeTrue();
    }

    [Fact]
    public void NullPath_AutoDetect_DoesNotFailEvenIfDirMissing()
    {
        // ~/.claude/projects under our temp home doesn't exist — and that's OK
        // for the auto-detect path. Validator emits no error; loader resolves
        // and lets the coordinator handle creation/watching.
        var config = BuildConfig("schema_version: 1");

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().NotThrow();
    }

    [Fact]
    public void Defaults_AppliedWhenKeysOmitted()
    {
        var config = BuildConfig("schema_version: 1");

        var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        resolved.Options.SubscriptionMode.Should().Be("enterprise");
        resolved.Options.SessionLogs.InitialScanEnabled.Should().BeTrue();
        resolved.Options.SessionLogs.InitialScanMaxAgeDays.Should().Be(30);
        resolved.Options.SessionLogs.ActiveSessionWindowMinutes.Should().Be(10);
        resolved.Options.Otlp.Endpoint.Should().Be("http://localhost:4317");
    }

    [Fact]
    public void InvalidLogLevel_Rejected()
    {
        var config = BuildConfig("""
            schema_version: 1
            logging:
              level: "Verbose"
            """);

        var act = () => TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempDir);

        act.Should().Throw<TokenScopeOptionsValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("logging.level 'Verbose'"));
    }

    [Fact]
    public void LoadFromFile_RealRepositoryExample_Loads()
    {
        var repoRoot = FindRepoRoot();
        var examplePath = Path.Combine(repoRoot, "config", "tokenscope.example.yaml");

        var act = () => TokenScopeOptionsLoader.LoadFromFile(examplePath, homeOverride: _tempDir);

        act.Should().NotThrow();
    }

    // ---- helpers ----

    private static IConfiguration BuildConfig(string yaml)
    {
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(yaml));
        var source = new YamlConfigurationSource { Optional = false };
        var builder = new ConfigurationBuilder().AddYamlStream(stream);
        return builder.Build();
    }

    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "TokenScope.sln")))
        {
            dir = dir.Parent;
        }
        return dir?.FullName ?? throw new InvalidOperationException("repo root not found");
    }
}
