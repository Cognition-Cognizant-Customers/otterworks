# Force the test environment before the Rails environment is loaded.
ENV['RAILS_ENV'] ||= 'test'
