using System.Collections.Immutable;
using System.Text.Json;
using TokenScope.Core.Domain;

namespace TokenScope.Core.Pricing;

public static class PricingLoader
{
    public const int SupportedSchemaVersion = 1;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    public static PricingTable LoadFromFile(string path, TimeProvider? clock = null)
    {
        using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        return LoadFromStream(stream, clock);
    }

    public static PricingTable LoadFromStream(Stream stream, TimeProvider? clock = null)
    {
        clock ??= TimeProvider.System;

        PricingConfigDto? dto;
        try
        {
            dto = JsonSerializer.Deserialize<PricingConfigDto>(stream, JsonOptions);
        }
        catch (JsonException ex)
        {
            throw new PricingValidationException(
                ImmutableArray.Create($"Pricing config is not valid JSON: {ex.Message}"));
        }

        if (dto is null)
        {
            throw new PricingValidationException(
                ImmutableArray.Create("Pricing config deserialized to null."));
        }

        return Build(dto, clock.GetUtcNow());
    }

    public static PricingTable LoadFromJson(string json, TimeProvider? clock = null)
    {
        using var stream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(json));
        return LoadFromStream(stream, clock);
    }

    private static PricingTable Build(PricingConfigDto dto, DateTimeOffset nowUtc)
    {
        var errors = ImmutableArray.CreateBuilder<string>();

        if (dto.SchemaVersion != SupportedSchemaVersion)
        {
            errors.Add($"schema_version {dto.SchemaVersion} is not supported (expected {SupportedSchemaVersion}).");
        }

        if (dto.Models is null || dto.Models.Count == 0)
        {
            errors.Add("models array is missing or empty.");
        }

        var entriesByModel = ImmutableDictionary.CreateBuilder<string, ImmutableArray<PricingEntry>>(StringComparer.Ordinal);

        if (dto.Models is not null)
        {
            for (var modelIndex = 0; modelIndex < dto.Models.Count; modelIndex++)
            {
                var model = dto.Models[modelIndex];
                var modelPath = $"models[{modelIndex}]";

                if (string.IsNullOrWhiteSpace(model.Id))
                {
                    errors.Add($"{modelPath}.id is missing or empty.");
                    continue;
                }

                if (entriesByModel.ContainsKey(model.Id))
                {
                    errors.Add($"{modelPath}.id '{model.Id}' is duplicated.");
                    continue;
                }

                if (model.Rates is null || model.Rates.Count == 0)
                {
                    errors.Add($"{modelPath} ('{model.Id}'): rates array is missing or empty.");
                    continue;
                }

                var entries = ImmutableArray.CreateBuilder<PricingEntry>(model.Rates.Count);
                var seenEffectiveDates = new HashSet<DateTimeOffset>();

                for (var rateIndex = 0; rateIndex < model.Rates.Count; rateIndex++)
                {
                    var rate = model.Rates[rateIndex];
                    var ratePath = $"{modelPath} ('{model.Id}').rates[{rateIndex}]";

                    if (rate.EffectiveDate is null)
                    {
                        errors.Add($"{ratePath}.effective_date is missing.");
                        continue;
                    }

                    var effectiveUtc = rate.EffectiveDate.Value.ToUniversalTime();
                    if (effectiveUtc > nowUtc)
                    {
                        errors.Add($"{ratePath}.effective_date {effectiveUtc:o} is in the future (now: {nowUtc:o}).");
                    }

                    if (!seenEffectiveDates.Add(effectiveUtc))
                    {
                        errors.Add($"{ratePath}.effective_date {effectiveUtc:o} duplicates another entry for '{model.Id}'.");
                    }

                    var input = ValidateRate(rate.InputPerMtok, $"{ratePath}.input_per_mtok", errors);
                    var output = ValidateRate(rate.OutputPerMtok, $"{ratePath}.output_per_mtok", errors);
                    var cacheRead = ValidateRate(rate.CacheReadPerMtok, $"{ratePath}.cache_read_per_mtok", errors);
                    var cacheWrite5m = ValidateRate(rate.CacheWrite5mPerMtok, $"{ratePath}.cache_write_5m_per_mtok", errors);
                    var cacheWrite1h = ValidateRate(rate.CacheWrite1hPerMtok, $"{ratePath}.cache_write_1h_per_mtok", errors);

                    if (input.HasValue && output.HasValue && cacheRead.HasValue
                        && cacheWrite5m.HasValue && cacheWrite1h.HasValue)
                    {
                        entries.Add(new PricingEntry(
                            effectiveUtc,
                            new ModelRate(
                                input.Value,
                                output.Value,
                                cacheRead.Value,
                                cacheWrite5m.Value,
                                cacheWrite1h.Value)));
                    }
                }

                entries.Sort(static (a, b) => a.EffectiveDate.CompareTo(b.EffectiveDate));
                entriesByModel[model.Id] = entries.ToImmutable();
            }
        }

        if (errors.Count > 0)
        {
            throw new PricingValidationException(errors.ToImmutable());
        }

        return new PricingTable(entriesByModel.ToImmutable())
        {
            Source = dto.Source,
            VerifiedAt = dto.VerifiedAt ?? default,
        };
    }

    private static decimal? ValidateRate(decimal? value, string path, ImmutableArray<string>.Builder errors)
    {
        if (value is null)
        {
            errors.Add($"{path} is missing.");
            return null;
        }

        if (value.Value < 0m)
        {
            errors.Add($"{path} is negative ({value.Value}).");
            return null;
        }

        return value.Value;
    }
}
