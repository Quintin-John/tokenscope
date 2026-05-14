namespace TokenScope.Core.Pricing;

public interface IPricingTable
{
    PricingLookupResult Lookup(string modelId, DateTimeOffset at);
}
