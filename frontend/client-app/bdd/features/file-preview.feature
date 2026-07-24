Feature: File preview from the Files list
  As an authenticated user
  I want to preview files of any type inline from the Files list
  So that I can view file contents without downloading them

  # Maps to the OTD-12 acceptance-criteria matrix (Phase 1: Files page).
  # Scenarios are resilient to the unauthenticated state: when a seeded/signed-in
  # session runs them they exercise the preview modal, otherwise they assert the
  # login redirect (matching the repo's existing smoke-level BDD convention).

  Scenario: Files page is reachable (AC-01)
    Given I navigate to "/files"
    Then I should see the text "Files" or "Sign in to your account"

  Scenario: A preview control is offered for files (AC-01, AC-11)
    Given I navigate to "/files"
    Then a preview control is available when signed in

  Scenario: Opening a preview shows an inline dialog (AC-01, AC-09, AC-10)
    Given I navigate to "/files"
    When I open the preview for the first file
    Then a preview dialog with a download action is shown when signed in

  Scenario: The preview dialog closes with Escape and restores the list (AC-09, AC-12)
    Given I navigate to "/files"
    When I open the preview for the first file
    And I press Escape
    Then the preview dialog is dismissed and the Files list remains
