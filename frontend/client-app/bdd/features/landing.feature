Feature: Landing Page
  As a prospective enterprise customer
  I want to see the OtterWorks corporate landing page
  So that I can learn about the company and sign up

  # AC-01 / BDD-01
  Scenario: Visitor sees the corporate hero with the otter logo
    Given I am on the landing page
    Then I should see the heading "OtterWorks"
    And I should see the otter logo
    And I should see the text "Enterprise retail products for otters"

  Scenario: Visitor sees navigation CTAs
    Given I am on the landing page
    Then I should see a link "Sign In"
    And I should see a link "Create Account"

  # AC-03 / BDD-07
  Scenario: Visitor sees the full corporate identity sections
    Given I am on the landing page
    Then I should see the text "Our Story"
    And I should see the text "Leadership"
    And I should see the text "Departments"
    And I should see the text "Products"
    And I should see the text "Newsroom"
    And I should see the text "Careers"

  Scenario: Sign In link navigates to login
    Given I am on the landing page
    When I click the link "Sign In"
    Then the URL should contain "/login"

  Scenario: Create Account link navigates to register
    Given I am on the landing page
    When I click the link "Create Account"
    Then the URL should contain "/register"

  # AC-02c / BDD-06
  Scenario: Corporate footer is visible with legal links
    Given I am on the landing page
    Then I should see the text "© OtterWorks, Inc."
    And I should see a link "Terms"
    And I should see a link "Privacy"

  # AC-02c / BDD-06
  Scenario: Terms and Privacy static pages are reachable
    Given I navigate to "/terms"
    Then I should see the heading "Terms of Service"
    Given I navigate to "/privacy"
    Then I should see the heading "Privacy Policy"
