using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using OtterWorks.AuditService.Services;

namespace AuditService.Tests.TestDoubles;

/// <summary>
/// Boots the real audit HTTP pipeline (endpoints + AuditService) over in-memory storage.
/// Each test gets its own factory instance, so no state is shared between tests.
/// </summary>
public sealed class AuditApiFactory : WebApplicationFactory<Program>
{
    public InMemoryAuditRepository Repository { get; } = new();

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Testing");

        builder.ConfigureServices(services =>
        {
            // The SQS/SNS consumer would poll AWS for the lifetime of the host.
            services.RemoveAll<IHostedService>();

            services.RemoveAll<IAuditRepository>();
            services.AddSingleton<IAuditRepository>(Repository);

            services.RemoveAll<IAuditArchiver>();
            services.AddSingleton<IAuditArchiver>(new StubAuditArchiver());
        });
    }
}
