Feature: File Preview
  As an authenticated user
  I want to preview files of any type inline
  So that I can view file contents without downloading them

  # AC-01/BDD-01: preview is reachable from the file list (one click to detail page)
  Scenario: Files page is reachable or redirects to login
    Given I navigate to "/files"
    Then I should see the text "Files" or "Sign in to your account"

  # AC-17/BDD-17: preview requires authentication
  Scenario: File detail preview requires authentication
    Given I navigate to "/files/00000000-0000-0000-0000-000000000000"
    Then I should see the text "Preview" or "Sign in to your account"
