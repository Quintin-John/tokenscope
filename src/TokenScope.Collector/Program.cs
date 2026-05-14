using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Configuration.EnvironmentVariables;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using NetEscapades.Configuration.Yaml;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using TokenScope.Collector.Configuration;
using TokenScope.Collector.Hosting;
using TokenScope.Core.Pricing;
using TokenScope.Otel.Configuration;
using TokenScope.Otel.Metrics;
using TokenScope.Otel.Tracking;

namespace TokenScope.Collector;

public static class Program
{
    public static int Main(string[] args)
    {
        // Pre-host flag: --validate-pricing <path>. Runs PricingLoader, prints
        // results, exits. Skips the hosted-service startup so CI use is cheap.
        // Exit codes: 0 success, 4 validation failure, 2 usage error.
        if (TryGetValidatePricingPath(args, out var pricingPath))
        {
            return RunValidatePricing(pricingPath);
        }

        try
        {
            var builder = Host.CreateApplicationBuilder(args);

            // Host.CreateApplicationBuilder adds an unprefixed env-var provider
            // that picks up every ambient env var (HOME, PATH, HOSTNAME, etc.).
            // In a container those flood the config tree and the strict-key
            // validator can't tell them apart from typos. Remove it; the
            // prefix-scoped TOKENSCOPE_ provider we add below is the only env
            // entry point we want.
            var ambient = builder.Configuration.Sources
                .OfType<EnvironmentVariablesConfigurationSource>()
                .Where(s => string.IsNullOrEmpty(s.Prefix))
                .ToList();
            foreach (var src in ambient)
            {
                builder.Configuration.Sources.Remove(src);
            }

            var yamlPath = ResolveYamlPath(args);
            builder.Configuration
                .SetBasePath(Directory.GetCurrentDirectory())
                .AddYamlFile(yamlPath, optional: false, reloadOnChange: false)
                // Env vars override YAML. Prefix-scoped so unrelated env vars
                // can't accidentally clobber config keys. Convention:
                //   TOKENSCOPE_<section>__<key>[__<subkey>]=value
                // e.g. TOKENSCOPE_SESSION_LOGS__PATH=/data/claude-logs
                .AddEnvironmentVariables(prefix: "TOKENSCOPE_");

            var resolved = TokenScopeOptionsLoader.LoadFromConfiguration(builder.Configuration);

            ConfigureLogging(builder, resolved);
            ConfigureServices(builder, resolved);

            using var host = builder.Build();
            host.Run();
            return 0;
        }
        catch (TokenScopeOptionsValidationException ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 2;
        }
        catch (FileNotFoundException ex)
        {
            Console.Error.WriteLine($"Configuration file not found: {ex.Message}");
            return 2;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Fatal: {ex.GetType().Name}: {ex.Message}");
            return 1;
        }
    }

    private static string ResolveYamlPath(string[] args)
    {
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i] is "--config" or "-c")
            {
                return args[i + 1];
            }
        }
        var envPath = Environment.GetEnvironmentVariable("TOKENSCOPE_CONFIG");
        return !string.IsNullOrWhiteSpace(envPath) ? envPath : "tokenscope.yaml";
    }

    /// <summary>
    /// Parses the <c>--validate-pricing &lt;path&gt;</c> flag. Returns true when
    /// the flag is present with a non-empty path.
    /// </summary>
    internal static bool TryGetValidatePricingPath(string[] args, out string path)
    {
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--validate-pricing")
            {
                if (i + 1 < args.Length && !string.IsNullOrWhiteSpace(args[i + 1]))
                {
                    path = args[i + 1];
                    return true;
                }
                Console.Error.WriteLine("--validate-pricing requires a path argument.");
                Environment.ExitCode = 2;
                path = "";
                return false;
            }
        }
        path = "";
        return false;
    }

    /// <summary>
    /// Runs <see cref="PricingLoader.LoadFromFile"/> on the given path and
    /// reports the result. Exit codes:
    ///   0  — pricing.json is valid
    ///   4  — validation failed; errors printed to stderr
    ///   2  — usage error (file not found, IO error)
    /// </summary>
    internal static int RunValidatePricing(string path)
    {
        if (!File.Exists(path))
        {
            Console.Error.WriteLine($"--validate-pricing: file not found: {path}");
            return 2;
        }

        try
        {
            var table = PricingLoader.LoadFromFile(path);
            Console.Out.WriteLine(
                $"OK  {path} — {table.KnownModelIds.Length} models, schema_version=1");
            return 0;
        }
        catch (PricingValidationException ex)
        {
            Console.Error.WriteLine($"FAIL  {path} — {ex.Errors.Length} validation error(s):");
            foreach (var err in ex.Errors)
            {
                Console.Error.WriteLine($"  - {err}");
            }
            return 4;
        }
        catch (IOException ex)
        {
            Console.Error.WriteLine($"--validate-pricing: I/O error reading {path}: {ex.Message}");
            return 2;
        }
    }

    private static void ConfigureLogging(HostApplicationBuilder builder, ResolvedTokenScopeOptions resolved)
    {
        if (Enum.TryParse<LogLevel>(resolved.Options.Logging.Level, ignoreCase: true, out var level))
        {
            builder.Logging.SetMinimumLevel(level);
        }
        builder.Logging.AddSimpleConsole(o =>
        {
            o.SingleLine = true;
            o.TimestampFormat = "yyyy-MM-ddTHH:mm:ss.fffZ ";
            o.UseUtcTimestamp = true;
        });
    }

    private static void ConfigureServices(HostApplicationBuilder builder, ResolvedTokenScopeOptions resolved)
    {
        builder.Services.AddSingleton(resolved);
        builder.Services.AddSingleton(resolved.Options);

        builder.Services.AddSingleton<PricingTableProvider>(_ =>
            new PricingTableProvider(
                resolved.PricingConfigPath,
                watch: resolved.Options.Pricing.HotReloadEnabled));
        builder.Services.AddSingleton<IPricingTable>(sp => sp.GetRequiredService<PricingTableProvider>());

        builder.Services.AddSingleton<ISessionActivityTracker, SessionActivityTracker>();
        builder.Services.AddSingleton<ICacheRatioSource, CacheRatioTracker>();

        builder.Services.AddSingleton(sp => new TokenScopeMetrics(
            sp.GetRequiredService<ISessionActivityTracker>(),
            sp.GetRequiredService<ICacheRatioSource>(),
            activeWindow: TimeSpan.FromMinutes(resolved.Options.SessionLogs.ActiveSessionWindowMinutes)));

        builder.Services
            .AddOpenTelemetry()
            .ConfigureResource(r => r.AddAttributes(new KeyValuePair<string, object>[]
            {
                new("service.name", "tokenscope"),
                new("tokenscope.subscription_mode", resolved.Options.SubscriptionMode),
            }))
            .WithMetrics(m =>
            {
                var otelOptions = new TokenScopeOtelOptions
                {
                    OtlpEndpoint = resolved.Options.Otlp.Endpoint,
                    Protocol = string.Equals(resolved.Options.Otlp.Protocol, "http", StringComparison.OrdinalIgnoreCase)
                        ? OtlpExportProtocol.HttpProtobuf
                        : OtlpExportProtocol.Grpc,
                    ActiveSessionWindow = TimeSpan.FromMinutes(resolved.Options.SessionLogs.ActiveSessionWindowMinutes),
                };
                m.AddTokenScopeMetricsWithOtlp(otelOptions);
            });

        builder.Services.AddHostedService<SessionLogCoordinator>();
    }
}
