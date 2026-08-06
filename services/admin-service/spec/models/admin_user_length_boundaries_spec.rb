require 'rails_helper'

RSpec.describe AdminUser do
  it_behaves_like 'a 255 character maximum attribute', :admin_user, :display_name
end
