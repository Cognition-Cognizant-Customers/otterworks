require 'rails_helper'

# PLANTED BUG (documented, deliberately NOT fixed — see AGENTS.md "Golden app policy"):
# config/environments/production.rb assigns
#   config.logger = ActiveSupport::TaggedLogging.logger($stdout)
# `ActiveSupport::TaggedLogging.logger` does not exist on the Rails version this
# service is pinned to, so booting RAILS_ENV=production raises NoMethodError and
# admin-service crash-loops. These examples pin that broken contract so it stays
# visible in the test output instead of only in a pod restart count.
# The subject is an environment configuration file rather than a class, hence the
# string description.
RSpec.describe 'production logger configuration' do # rubocop:disable RSpec/DescribeClass
  it 'rails_version_is_the_7_1_series_the_service_is_pinned_to' do
    expect(Rails.version).to start_with('7.1.')
  end

  it 'tagged_logging_does_not_expose_a_logger_factory_on_this_rails_version' do
    expect(ActiveSupport::TaggedLogging).not_to respond_to(:logger)
  end

  it 'tagged_logging_logger_with_an_io_argument_raises_nomethoderror' do
    expect { ActiveSupport::TaggedLogging.logger($stdout) }.to raise_error(NoMethodError, /undefined method .logger./)
  end

  it 'tagged_logging_new_with_a_logger_argument_is_the_supported_api_on_this_version' do
    expect(ActiveSupport::TaggedLogging.new(Logger.new(File::NULL))).to respond_to(:tagged)
  end

  it 'production_environment_file_still_contains_the_planted_call' do
    production_config = Rails.root.join('config/environments/production.rb').read

    expect(production_config).to include('ActiveSupport::TaggedLogging.logger($stdout)')
  end
end
