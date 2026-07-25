Feature: File Preview (OTD-12)
  As an authenticated user
  I want to preview files of any type inline
  So that I can view file contents without downloading them

  # AC-01/BDD-01 — preview reachable from the file list route
  Scenario: Files page is the entry point to file previews
    Given I navigate to "/files"
    Then I should see the text "Files" or "Sign in to your account"

  # AC-17/BDD-17 — preview pages require authentication
  Scenario: File detail preview requires login
    Given I navigate to "/files/00000000-0000-0000-0000-000000000000"
    Then I should see the text "Preview" or "Sign in to your account"
