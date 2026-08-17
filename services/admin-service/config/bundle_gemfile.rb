# Point Bundler at this service's Gemfile before `bundler/setup` is required.
ENV['BUNDLE_GEMFILE'] ||= File.expand_path('../Gemfile', __dir__)
