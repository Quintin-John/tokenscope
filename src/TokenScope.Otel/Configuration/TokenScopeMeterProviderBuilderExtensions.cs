using OpenTelemetry.Metrics;
using TokenScope.Otel.Metrics;

namespace TokenScope.Otel.Configuration;

public static class TokenScopeMeterProviderBuilderExtensions
{
    /// <summary>
    /// Registers the <c>tokenscope</c> meter on the
    /// <see cref="MeterProviderBuilder"/> without configuring an exporter.
    /// Use this in tests when an in-memory exporter (or
    /// <see cref="System.Diagnostics.Metrics.MeterListener"/>) is wired
    /// separately.
    /// </summary>
    public static MeterProviderBuilder AddTokenScopeMeter(this MeterProviderBuilder builder)
    {
        ArgumentNullException.ThrowIfNull(builder);
        return builder.AddMeter(TokenScopeMetrics.MeterName);
    }

    /// <summary>
    /// Registers the <c>tokenscope</c> meter and an OTLP exporter pointed
    /// at the endpoint in <paramref name="options"/>. This is the
    /// production wiring used by the Collector host.
    /// </summary>
    public static MeterProviderBuilder AddTokenScopeMetricsWithOtlp(
        this MeterProviderBuilder builder,
        TokenScopeOtelOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(builder);
        var opts = options ?? new TokenScopeOtelOptions();

        return builder
            .AddTokenScopeMeter()
            .AddOtlpExporter(exporter =>
            {
                exporter.Endpoint = new Uri(opts.OtlpEndpoint);
                exporter.Protocol = opts.Protocol;
            });
    }
}
