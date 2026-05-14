using System.Text.Json.Serialization;

namespace TokenScope.Core.Pricing;

internal sealed class PricingConfigDto
{
    public int SchemaVersion { get; set; }
    public string? Source { get; set; }
    public DateTimeOffset? VerifiedAt { get; set; }
    public string? Currency { get; set; }
    public string? Notes { get; set; }
    public List<ModelDto>? Models { get; set; }
}

internal sealed class ModelDto
{
    public string? Id { get; set; }
    public List<RateDto>? Rates { get; set; }
}

internal sealed class RateDto
{
    public DateTimeOffset? EffectiveDate { get; set; }
    public decimal? InputPerMtok { get; set; }
    public decimal? OutputPerMtok { get; set; }
    public decimal? CacheReadPerMtok { get; set; }

    // SnakeCaseLower maps "CacheWrite5mPerMtok" -> "cache_write5m_per_mtok"
    // (no underscore before a digit). The pricing file format uses an underscore
    // there, so we pin the JSON name explicitly.
    [JsonPropertyName("cache_write_5m_per_mtok")]
    public decimal? CacheWrite5mPerMtok { get; set; }

    [JsonPropertyName("cache_write_1h_per_mtok")]
    public decimal? CacheWrite1hPerMtok { get; set; }
}
