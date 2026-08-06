using Amazon.S3;
using Amazon.S3.Model;
using Moq;

namespace AuditService.Tests.Support;

/// <summary>Captures the objects the archiver would have written, with no live S3 or LocalStack.</summary>
public sealed class FakeS3
{
    public FakeS3()
    {
        Mock = new Mock<IAmazonS3>(MockBehavior.Strict);
        Mock.Setup(s => s.PutObjectAsync(It.IsAny<PutObjectRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((PutObjectRequest req, CancellationToken _) =>
            {
                PutRequests.Add(req);
                return new PutObjectResponse();
            });
    }

    public Mock<IAmazonS3> Mock { get; }

    public IAmazonS3 Client => Mock.Object;

    public List<PutObjectRequest> PutRequests { get; } = new();

    public PutObjectRequest LastPut => PutRequests[^1];
}
