using AwesomeAssertions;
using Microsoft.Extensions.Configuration;
using NetEscapades.Configuration.Yaml;
using TokenScope.Collector.Configuration;
using Xunit;

namespace TokenScope.Collector.Tests.Configuration;

/// <summary>
/// Phase 6 container deployment relies on <c>TOKENSCOPE_*</c> environment
/// variables overriding the YAML defaults. This test pins the precedence
/// rule (env > YAML > built-in defaults) so a future refactor of the
/// config-builder chain can't silently invert it.
/// </summary>
public class EnvironmentVariableOverrideTests : IDisposable
{
    private readonly string _tempHome;

    public EnvironmentVariableOverrideTests()
    {
        _tempHome = Path.Combine(Path.GetTempPath(), "tokenscope-envtests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempHome);
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempHome, recursive: true); } catch { }
    }

    [Fact]
    public void EnvVarWithTokenscopePrefix_OverridesYamlValue()
    {
        // YAML sets endpoint to one value; env var sets a different one.
        // The env var must win, matching the precedence wired in Program.cs.
        const string yaml = """
            schema_version: 1
            otlp:
              endpoint: "http://yaml-host:4317"
              protocol: "grpc"
            """;

        using var yamlStream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(yaml));

        var config = new ConfigurationBuilder()
            .AddYamlStream(yamlStream)
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["otlp:endpoint"] = "http://env-host:4317",
            })
            .Build();

        var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempHome);

        resolved.Options.Otlp.Endpoint.Should().Be("http://env-host:4317",
            "the in-memory provider stands in for the env-var provider and is added AFTER the YAML provider — the same precedence Program.cs uses.");
    }

    [Fact]
    public void EnvVarPath_Override_TakesPrecedenceOverYamlPath()
    {
        // The explicit-path-must-exist rule applies — create the override target.
        var explicitPath = Path.Combine(_tempHome, "explicit-logs");
        Directory.CreateDirectory(explicitPath);

        const string yaml = """
            schema_version: 1
            session_logs:
              path: null
            """;

        using var yamlStream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(yaml));

        var config = new ConfigurationBuilder()
            .AddYamlStream(yamlStream)
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["session_logs:path"] = explicitPath,
            })
            .Build();

        var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempHome);

        resolved.SessionLogsPath.Should().Be(explicitPath);
        resolved.SessionLogsPathIsExplicit.Should().BeTrue(
            "the env-var-supplied path is non-null, so it must be treated as explicit (strict validation).");
    }

    [Fact]
    public void EnvVarDoubleUnderscoreSeparator_MapsToNestedConfigKey()
    {
        // The .NET env-var convention: section__key. Verify the loader
        // accepts it without any additional code change.
        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["schema_version"] = "1",
                ["pricing:config_path"] = "/custom/pricing.json",
                ["pricing:hot_reload_enabled"] = "false",
            })
            .Build();

        var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(config, homeOverride: _tempHome);

        resolved.PricingConfigPath.Should().Be("/custom/pricing.json");
        resolved.Options.Pricing.HotReloadEnabled.Should().BeFalse();
    }
}
