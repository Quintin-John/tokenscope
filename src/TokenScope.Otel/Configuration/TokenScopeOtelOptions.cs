using OpenTelemetry.Exporter;

namespace TokenScope.Otel.Configuration;

public sealed record TokenScopeOtelOptions
{
    /// <summary>OTLP exporter endpoint. Default is the OTEL Collector's
    /// canonical gRPC port on localhost.</summary>
    public string OtlpEndpoint { get; init; } = "http://localhost:4317";

    /// <summary>OTLP protocol. gRPC is the project default; HTTP/protobuf
    /// is the most common alternative.</summary>
    public OtlpExportProtocol Protocol { get; init; } = OtlpExportProtocol.Grpc;

    /// <summary>Sliding window for <c>tokenscope.sessions.active</c>. A
    /// session is counted as active if its most recent event is within
    /// this window of the current time.</summary>
    public TimeSpan ActiveSessionWindow { get; init; } = TimeSpan.FromMinutes(10);
}
