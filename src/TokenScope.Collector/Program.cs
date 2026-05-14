using Microsoft.Extensions.Configuration;
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
        try
        {
            var builder = Host.CreateApplicationBuilder(args);

            var yamlPath = ResolveYamlPath(args);
            builder.Configuration
                .SetBasePath(Directory.GetCurrentDirectory())
                .AddYamlFile(yamlPath, optional: false, reloadOnChange: false);

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
