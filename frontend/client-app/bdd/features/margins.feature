Feature: Margins analytics dashboard
  As a merchandising analyst
  I want a margins dashboard driven by supply-chain market data
  So that I can monitor SKU profitability (OTD-15: AC-08, AC-11, AC-12, AC-13)

  # BDD-12 / AC-12: direct route works (redirects to login when unauthenticated)
  Scenario: Margins page loads or redirects
    Given I navigate to "/margins"
    Then I should see the text "Margins" or "Sign in to your account"

  # BDD-08 / AC-08 + BDD-13 / AC-13: full dashboard renders from synthetic data
  Scenario: Authenticated user sees the full margins dashboard
    Given I am logged in as a new user
    When I navigate to "/margins"
    Then I should see the heading "Margins"
    And I should see the text "Gross Margin %"
    And I should see the text "COGS / unit"
    And I should see the text "Salmon Index"
    And I should see the text "Freight Index"
    And I should see the margins data caption
    And I should see the margins grid with rows
    And I should see a "Export CSV" button

  # BDD-13 / AC-13: synthetic source badge on a fresh stack
  Scenario: Source badge shows the data source
    Given I am logged in as a new user
    When I navigate to "/margins"
    Then I should see the margins source badge

  # BDD-12 / AC-12: sidebar entry navigates to /margins
  Scenario: Sidebar Margins entry navigates to the dashboard
    Given I am logged in as a new user
    When I navigate to "/dashboard"
    And I click the link "Margins"
    Then I should be on the "/margins" page
    And I should see the heading "Margins"
