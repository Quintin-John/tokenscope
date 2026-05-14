namespace TokenScope.Core.Domain;

public abstract record CostResult
{
    private CostResult() { }

    public sealed record Success(Cost Cost) : CostResult;

    public sealed record ModelNotFound(string ModelId, DateTimeOffset RequestedAt) : CostResult;

    public sealed record NoRateEffective(string ModelId, DateTimeOffset RequestedAt) : CostResult;
}
