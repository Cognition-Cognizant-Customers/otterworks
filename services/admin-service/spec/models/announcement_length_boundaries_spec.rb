require 'rails_helper'

RSpec.describe Announcement do
  it_behaves_like 'a 255 character maximum attribute', :announcement, :title
end
