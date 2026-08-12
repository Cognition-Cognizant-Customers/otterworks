require 'jwt'
require 'net/http'
require 'securerandom'

# Generates synthetic traffic to chaos-affected services so that Prometheus
# metrics accumulate and Grafana alerts fire without needing real user traffic.
#
# When chaos is triggered, a background thread pings the affected endpoint
# every few seconds until the Redis chaos key expires or is manually reset.
class ChaosProbeService
  PROBE_INTERVAL = 5 # seconds between probe requests
  PROBE_BATCH    = 3 # requests per interval (enough for rate() to register)
  SERVICE_TOKEN_TTL = 300 # seconds a minted probe token stays valid

  SERVICE_PROBES = {
    'search-service' => {
      url: 'http://search-service:8087/api/v1/search/suggest?q=test',
      headers: { 'X-User-ID' => 'chaos-probe' },
    },
    # file-service upload expects multipart/form-data with a "file" field.
    # Sending JSON results in a 400 before the chaos flag is ever checked.
    # X-User-ID must be a valid UUID (parsed by the handler).
    'file-service' => {
      url: 'http://file-service:8082/api/v1/files/upload',
      method: :multipart,
      headers: { 'X-User-ID' => '00000000-0000-0000-0000-000000000001' },
    },
    # notification-service chaos works by switching to a strict JSON parser
    # that rejects messages with integer (Unix epoch) timestamps.  Hitting
    # /health does nothing — we need to push a malformed message into the SQS
    # queue so the consumer increments its error counter.
    'notification-service' => {
      url: 'http://localstack:4566/000000000000/otterworks-notifications',
      method: :sqs,
      headers: {},
    },
    # document-service chaos injects 3-5s latency before every DB query.
    # The probe hits GET /api/v1/documents so the slow response times show
    # up in Prometheus histograms and trigger the P95 latency alert.
    # Listing is scoped to the authenticated caller, so the probe presents a
    # service-scoped token rather than an identity header.
    'document-service' => {
      url: 'http://document-service:8083/api/v1/documents/',
      headers: {},
      service_token: true,
      read_timeout: 8,
    },
  }.freeze

  # Starts a background probe thread for the given service.
  # The thread self-terminates when the Redis key no longer exists.
  def self.start(service:, redis_key:)
    probe_config = SERVICE_PROBES[service]
    return unless probe_config

    Thread.new do
      Thread.current.report_on_exception = true
      Rails.logger.info("[ChaosProbe] Started for #{service}")
      redis = Redis.new(
        url: ENV.fetch('REDIS_URL', "redis://#{ENV.fetch('REDIS_HOST', 'localhost')}:#{ENV.fetch('REDIS_PORT', '6379')}/0"),
        timeout: 2
      )

      iterations = 0
      loop do
        break unless redis.exists?(redis_key)

        PROBE_BATCH.times { fire_probe(probe_config) }
        iterations += 1
        sleep PROBE_INTERVAL
      end

      Rails.logger.info("[ChaosProbe] Stopped for #{service} after #{iterations} iterations")
    rescue StandardError => e
      Rails.logger.error("[ChaosProbe] Thread error for #{service}: #{e.class} - #{e.message}")
    ensure
      redis&.close
    end
  end

  def self.fire_probe(config)
    uri = URI.parse(config[:url])
    http = Net::HTTP.new(uri.host, uri.port)
    http.open_timeout = 3
    http.read_timeout = config.fetch(:read_timeout, 3)

    request = case config[:method]
              when :multipart
                build_multipart_request(uri)
              when :sqs
                build_sqs_request(uri)
              when :post
                req = Net::HTTP::Post.new(uri.request_uri)
                req.body = config[:body] if config[:body]
                req
              else
                Net::HTTP::Get.new(uri.request_uri)
              end

    config[:headers]&.each { |k, v| request[k] = v }
    if config[:service_token]
      token = service_token
      request['Authorization'] = "Bearer #{token}" if token
    end
    http.request(request)
  rescue StandardError
    nil
  end

  # Mints a short-lived service-scoped token for probes against endpoints that
  # require authentication. Returns nil when no signing secret is configured.
  def self.service_token
    secret = Rails.application.credentials.jwt_secret || ENV['JWT_SECRET']
    return nil if secret.blank?

    JWT.encode(
      { 'scope' => 'service', 'sub' => 'admin-service-chaos-probe',
        'exp' => (Time.now + SERVICE_TOKEN_TTL).to_i },
      secret,
      'HS256'
    )
  end

  # Builds a multipart/form-data POST with a small dummy file.
  def self.build_multipart_request(uri)
    boundary = "chaos-probe-#{SecureRandom.hex(8)}"
    body = "--#{boundary}\r\n" \
           "Content-Disposition: form-data; name=\"file\"; filename=\"probe.txt\"\r\n" \
           "Content-Type: text/plain\r\n" \
           "\r\n" \
           "chaos probe\r\n" \
           "--#{boundary}--\r\n"

    req = Net::HTTP::Post.new(uri.request_uri)
    req['Content-Type'] = "multipart/form-data; boundary=#{boundary}"
    req.body = body
    req
  end

  # Sends an SQS SendMessage request with a malformed timestamp (integer
  # instead of RFC 3339 string).  The notification-service strict-schema chaos
  # parser rejects this, increments its error counter, and leaves the message
  # in the queue — exactly the scenario the alert is designed to detect.
  def self.build_sqs_request(uri)
    malformed_message = {
      eventType: 'file_shared',
      fileId: SecureRandom.uuid,
      ownerId: SecureRandom.uuid,
      sharedWithUserId: SecureRandom.uuid,
      timestamp: Time.now.to_i, # integer epoch — strict parser rejects this
    }.to_json

    body = URI.encode_www_form(
      'Action'          => 'SendMessage',
      'MessageBody'     => malformed_message,
      'Version'         => '2012-11-05',
    )

    req = Net::HTTP::Post.new(uri.request_uri)
    req['Content-Type'] = 'application/x-www-form-urlencoded'
    req.body = body
    req
  end
end
