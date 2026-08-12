FROM ruby:3.4-slim AS builder

RUN apt-get update && apt-get install -y build-essential libpq-dev libyaml-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Gemfile Gemfile.lock ./
RUN bundle config set --local without 'test development' && bundle install

FROM ruby:3.4-slim
RUN apt-get update && apt-get install -y libpq5 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /usr/local/bundle /usr/local/bundle
COPY . .

RUN useradd -r -u 1001 appuser && mkdir -p /app/tmp/pids /app/tmp/cache /app/log && chown -R appuser:appuser /app/tmp /app/log
USER appuser

EXPOSE 8089
HEALTHCHECK --interval=15s --timeout=5s CMD curl -f http://localhost:8089/health || exit 1
CMD ["sh", "-c", "bundle exec rails db:migrate; bundle exec rails server -b 0.0.0.0 -p 8089"]
