require 'rails_helper'

RSpec.describe Incident do
  it_behaves_like 'a 255 character maximum attribute', :incident, :title
end
