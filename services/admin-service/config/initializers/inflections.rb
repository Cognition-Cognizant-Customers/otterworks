# Intentionally empty — avoid acronym inflections that conflict with
# Zeitwerk's default snake_case-to-CamelCase mapping.
# The codebase uses Api (not API) and Jwt (not JWT) in class names,
# so no acronym overrides are needed.
ActiveSupport::Inflector.inflections(:en) do |_inflect|
end
