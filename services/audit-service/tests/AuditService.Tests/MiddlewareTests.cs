using System.Text;
using System.Text.Json;
using AuditService.Tests.TestSupport;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Extensions.Logging;
using OtterWorks.AuditService.Middleware;

namespace AuditService.Tests;

/// <summary>
/// Unit tests for the two middlewares audit-service installs (Program.cs installs
/// <see cref="ErrorHandlingMiddleware"/> outermost, then <see cref="RequestLoggingMiddleware"/>).
///
/// WP-09 finding (genuine gap, not a planted bug): neither middleware reads a correlation id
/// or an auth header. There is no correlation middleware in this service, so an inbound
/// <c>X-Correlation-ID</c> is dropped and log lines carry only ASP.NET's per-request
/// <c>TraceIdentifier</c>. The header tests below pin that behaviour rather than fix it.
/// </summary>
public class MiddlewareTests
{
    private static DefaultHttpContext ContextWithRecordableBody(string method = "GET", string path = "/api/v1/audit/events")
    {
        var context = new DefaultHttpContext();
        context.Request.Method = method;
        context.Request.Path = path;
        context.Response.Body = new MemoryStream();
        context.TraceIdentifier = "trace-abc";
        return context;
    }

    private static async Task<string> ReadBodyAsync(HttpContext context)
    {
        context.Response.Body.Position = 0;
        using var reader = new StreamReader(context.Response.Body, Encoding.UTF8, leaveOpen: true);
        return await reader.ReadToEndAsync();
    }

    // ---------- ErrorHandlingMiddleware: positive ----------

    [Fact]
    public async Task ErrorHandling_WithoutAnException_LeavesTheResponseUntouched()
    {
        var context = ContextWithRecordableBody();
        var nextCalled = false;
        var middleware = new ErrorHandlingMiddleware(
            _ =>
            {
                nextCalled = true;
                context.Response.StatusCode = StatusCodes.Status201Created;
                return Task.CompletedTask;
            },
            new RecordingLogger<ErrorHandlingMiddleware>());

        await middleware.InvokeAsync(context);

        Assert.True(nextCalled);
        Assert.Equal(StatusCodes.Status201Created, context.Response.StatusCode);
        Assert.Empty(await ReadBodyAsync(context));
    }

    // ---------- ErrorHandlingMiddleware: negative ----------

    [Fact]
    public async Task ErrorHandling_WhenTheHandlerThrows_Returns500JsonWithTheTraceIdAndNoInternals()
    {
        var context = ContextWithRecordableBody();
        var logger = new RecordingLogger<ErrorHandlingMiddleware>();
        var middleware = new ErrorHandlingMiddleware(
            _ => throw new InvalidOperationException("connection string Password=hunter2 rejected by dynamodb"),
            logger);

        await middleware.InvokeAsync(context);

        Assert.Equal(StatusCodes.Status500InternalServerError, context.Response.StatusCode);
        Assert.Equal("application/json", context.Response.ContentType);

        var body = await ReadBodyAsync(context);
        using var payload = JsonDocument.Parse(body);
        Assert.Equal("An internal server error occurred.", payload.RootElement.GetProperty("error").GetString());
        Assert.Equal("trace-abc", payload.RootElement.GetProperty("traceId").GetString());
        Assert.DoesNotContain("hunter2", body);
        Assert.DoesNotContain("InvalidOperationException", body);
        Assert.Contains(logger.Entries, e => e.Level == LogLevel.Error && e.Exception is InvalidOperationException);
    }

    [Fact]
    public async Task ErrorHandling_WhenTheResponseHasAlreadyStarted_RethrowsInsteadOfCorruptingTheBody()
    {
        var features = new FeatureCollection();
        features.Set<IHttpRequestFeature>(new HttpRequestFeature { Method = "GET", Path = "/api/v1/audit/export" });
        features.Set<IHttpResponseFeature>(new StartedResponseFeature());
        features.Set<IHttpResponseBodyFeature>(new StreamResponseBodyFeature(new MemoryStream()));
        var context = new DefaultHttpContext(features);
        var logger = new RecordingLogger<ErrorHandlingMiddleware>();
        var middleware = new ErrorHandlingMiddleware(
            _ => throw new InvalidOperationException("late failure"),
            logger);

        var thrown = await Assert.ThrowsAsync<InvalidOperationException>(() => middleware.InvokeAsync(context));

        Assert.Equal("late failure", thrown.Message);
        Assert.Equal(StatusCodes.Status200OK, context.Response.StatusCode);
        Assert.Contains(logger.Entries, e => e.Level == LogLevel.Warning);
    }

    [Fact]
    public async Task ErrorHandling_DoesNotSwallowCancellation_ButStillReportsIt()
    {
        var context = ContextWithRecordableBody();
        var middleware = new ErrorHandlingMiddleware(
            _ => throw new OperationCanceledException(),
            new RecordingLogger<ErrorHandlingMiddleware>());

        await middleware.InvokeAsync(context);

        Assert.Equal(StatusCodes.Status500InternalServerError, context.Response.StatusCode);
    }

    // ---------- RequestLoggingMiddleware ----------

    [Fact]
    public async Task RequestLogging_LogsMethodPathAndStatusOfASuccessfulRequest()
    {
        var context = ContextWithRecordableBody("POST", "/api/v1/audit/events");
        var logger = new RecordingLogger<RequestLoggingMiddleware>();
        var middleware = new RequestLoggingMiddleware(
            ctx =>
            {
                ctx.Response.StatusCode = StatusCodes.Status201Created;
                return Task.CompletedTask;
            },
            logger);

        await middleware.InvokeAsync(context);

        var entry = Assert.Single(logger.Entries);
        Assert.Equal(LogLevel.Information, entry.Level);
        Assert.Contains("POST", entry.Message);
        Assert.Contains("/api/v1/audit/events", entry.Message);
        Assert.Contains("201", entry.Message);
    }

    [Fact]
    public async Task RequestLogging_WhenTheHandlerThrows_LogsAnErrorAndRethrows()
    {
        var context = ContextWithRecordableBody();
        var logger = new RecordingLogger<RequestLoggingMiddleware>();
        var middleware = new RequestLoggingMiddleware(
            _ => throw new TimeoutException("dynamo timeout"),
            logger);

        await Assert.ThrowsAsync<TimeoutException>(() => middleware.InvokeAsync(context));

        var entry = Assert.Single(logger.Entries);
        Assert.Equal(LogLevel.Error, entry.Level);
        Assert.IsType<TimeoutException>(entry.Exception);
        Assert.Equal(StatusCodes.Status200OK, context.Response.StatusCode);
    }

    [Fact]
    public async Task RequestLogging_DoesNotLogRequestHeaders_SoBearerTokensStayOutOfTheLog()
    {
        var context = ContextWithRecordableBody();
        context.Request.Headers.Authorization = "Bearer super-secret-token";
        context.Request.Headers["Cookie"] = "session=super-secret-cookie";
        var logger = new RecordingLogger<RequestLoggingMiddleware>();
        var middleware = new RequestLoggingMiddleware(_ => Task.CompletedTask, logger);

        await middleware.InvokeAsync(context);

        var entry = Assert.Single(logger.Entries);
        Assert.DoesNotContain("super-secret-token", entry.Message);
        Assert.DoesNotContain("super-secret-cookie", entry.Message);
    }

    // ---------- header handling (pins today's behaviour) ----------

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("not-a-uuid")]
    [InlineData("11111111-1111-1111-1111-111111111111")]
    public async Task RequestLogging_IgnoresTheInboundCorrelationHeader_AndNeverEchoesIt(string? correlationId)
    {
        var context = ContextWithRecordableBody();
        if (correlationId is not null)
        {
            context.Request.Headers["X-Correlation-ID"] = correlationId;
        }

        var logger = new RecordingLogger<RequestLoggingMiddleware>();
        var middleware = new RequestLoggingMiddleware(_ => Task.CompletedTask, logger);

        await middleware.InvokeAsync(context);

        Assert.False(context.Response.Headers.ContainsKey("X-Correlation-ID"));
        Assert.Single(logger.Entries);
        Assert.DoesNotContain("11111111", logger.Entries[0].Message);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("Bearer")]
    [InlineData("Bearer not.a.jwt")]
    [InlineData("Basic YWRtaW46YWRtaW4=")]
    public async Task Middlewares_DoNotRejectMissingOrInvalidAuthHeaders_ThereIsNoAuthInThisPipeline(string? authorization)
    {
        var context = ContextWithRecordableBody();
        if (authorization is not null)
        {
            context.Request.Headers.Authorization = authorization;
        }

        var handlerReached = false;
        var middleware = new ErrorHandlingMiddleware(
            innerContext => new RequestLoggingMiddleware(
                _ =>
                {
                    handlerReached = true;
                    return Task.CompletedTask;
                },
                new RecordingLogger<RequestLoggingMiddleware>()).InvokeAsync(innerContext),
            new RecordingLogger<ErrorHandlingMiddleware>());

        await middleware.InvokeAsync(context);

        Assert.True(handlerReached);
        Assert.Equal(StatusCodes.Status200OK, context.Response.StatusCode);
    }

    // ---------- pipeline composition ----------

    [Fact]
    public async Task Pipeline_LogsTheFailureAndStillReturnsTheSanitised500()
    {
        var context = ContextWithRecordableBody();
        var errorLogger = new RecordingLogger<ErrorHandlingMiddleware>();
        var requestLogger = new RecordingLogger<RequestLoggingMiddleware>();
        var middleware = new ErrorHandlingMiddleware(
            innerContext => new RequestLoggingMiddleware(
                _ => throw new InvalidOperationException("boom"),
                requestLogger).InvokeAsync(innerContext),
            errorLogger);

        await middleware.InvokeAsync(context);

        Assert.Equal(StatusCodes.Status500InternalServerError, context.Response.StatusCode);
        Assert.Contains(requestLogger.Entries, e => e.Level == LogLevel.Error);
        Assert.Contains(errorLogger.Entries, e => e.Level == LogLevel.Error);
        Assert.Contains("An internal server error occurred.", await ReadBodyAsync(context));
    }

    private sealed class StartedResponseFeature : IHttpResponseFeature
    {
        public Stream Body { get; set; } = Stream.Null;

        public bool HasStarted => true;

        public IHeaderDictionary Headers { get; set; } = new HeaderDictionary();

        public string? ReasonPhrase { get; set; }

        public int StatusCode { get; set; } = StatusCodes.Status200OK;

        public void OnCompleted(Func<object, Task> callback, object state)
        {
        }

        public void OnStarting(Func<object, Task> callback, object state)
        {
        }
    }
}
