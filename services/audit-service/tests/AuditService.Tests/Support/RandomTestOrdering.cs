using System.Globalization;
using AuditService.Tests.Support;
using Xunit.Abstractions;
using Xunit.Sdk;

[assembly: TestCaseOrderer("AuditService.Tests.Support.RandomTestCaseOrderer", "AuditService.Tests")]
[assembly: TestCollectionOrderer("AuditService.Tests.Support.RandomTestCollectionOrderer", "AuditService.Tests")]

namespace AuditService.Tests.Support;

/// <summary>
/// Shuffles execution order so ordering dependencies between cases surface immediately.
/// The permutation is derived from <c>AUDIT_TEST_SEED</c> (or a fresh random seed) so a run
/// can be reproduced by re-exporting the seed.
/// </summary>
internal static class TestOrderSeed
{
    public static int Value { get; } =
        int.TryParse(
            Environment.GetEnvironmentVariable("AUDIT_TEST_SEED"),
            NumberStyles.Integer,
            CultureInfo.InvariantCulture,
            out var seed)
            ? seed
            : Random.Shared.Next();

    public static uint Shuffle(string key)
    {
        unchecked
        {
            var hash = 2166136261u ^ (uint)Value;
            foreach (var c in key)
            {
                hash = (hash ^ c) * 16777619u;
            }

            return hash;
        }
    }
}

public sealed class RandomTestCaseOrderer : ITestCaseOrderer
{
    public IEnumerable<TTestCase> OrderTestCases<TTestCase>(IEnumerable<TTestCase> testCases)
        where TTestCase : ITestCase =>
        testCases
            .OrderBy(tc => TestOrderSeed.Shuffle(tc.UniqueID))
            .ThenBy(tc => tc.UniqueID, StringComparer.Ordinal)
            .ToList();
}

public sealed class RandomTestCollectionOrderer : ITestCollectionOrderer
{
    public IEnumerable<ITestCollection> OrderTestCollections(IEnumerable<ITestCollection> testCollections) =>
        testCollections
            .OrderBy(tc => TestOrderSeed.Shuffle(tc.UniqueID.ToString()))
            .ThenBy(tc => tc.UniqueID)
            .ToList();
}
