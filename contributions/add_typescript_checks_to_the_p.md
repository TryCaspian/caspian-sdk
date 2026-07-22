```markdown
# Pull Request Checklist (Contributor Guide)

Thank you for your contribution! Please review this checklist carefully before submitting your pull request. Ensuring all checks pass helps maintain code quality and expedites the merge process.

---

### 🛡️ General Development Checks (Mandatory)

Please ensure that *all* of the following tests have been run locally and passed successfully:

- [ ] **Core Tests:** Run unit/integration tests using Pytest (`pytest`).
- [ ] **Linting & Formatting:** Check for code quality issues using Ruff (`ruff check --exit-base 0`).
- [ ] **TypeScript Type Checking:** Run the TypeScript type checker to ensure all SDK boundaries and definitions are correct (Applicable if touching `sdks/typescript/*`):
    ```bash
    npm run typecheck:ts # Assumes this script is defined in sdks/typescript/package.json
    # Alternatively, depending on setup: npx tsc --noEmit
    ```
- [ ] **TypeScript Test Suite:** Run the dedicated TypeScript SDK test suite to validate functionality and bindings (Applicable if touching `sdks/typescript/*`):
    ```bash
    npm run test:ts # Assumes this script is defined in sdks/typescript/package.json
    # Alternatively, depending on setup: jest --config=jest-ts.json
    ```

---

### 🔗 Module Specific Checks

#### Python Adapter & Backend Logic (`adapters/*`)
- [ ] **API Compatibility:** Confirm all API calls adhere to the latest schema definitions and ensure no breaking changes in adapter logic.
- [ ] **Data Model Integrity:** Verify that data model migrations (if applicable) are correctly handled and idempotent.

#### Security Implementation
*If your change involves handling sensitive data, authentication, or network operations:*
- [ ] **Input Validation:** Have all user inputs been thoroughly validated against expected types and formats?
- [ ] **Secrets Management:** No hardcoded credentials, API keys, or secrets have been introduced into the codebase.
- [ ] **Vulnerability Review:** Potential race conditions, injection vectors (SQL/Command), or insecure serialization paths have been addressed.

#### TypeScript SDK Layer (`sdks/typescript/*`)
*If your change modifies any bindings, types, or utilizes Node APIs:*
- [ ] **Type Safety Enforcement:** Has the full type check passed? Are all public interfaces correctly typed and documented via JSDoc/TSDoc?
- [ ] **Environment Consideration:** Does the code account for potential differences between runtime environments (e.g., browser vs. Node)?
- [ ] **Immutability:** Have complex data structures been appropriately handled regarding mutability to prevent unintended side effects?

---

### 🚀 Final Review & Commit Details

- [ ] **Documentation:** If new features or major changes were added, have they been documented in the relevant `README` or API reference files?
- [ ] **Code Clarity:** Is the code self-explanatory? Are comments added where complex logic is implemented?
- [ ] **Testing Coverage:** Was a test (unit, integration, or e2e) written to cover this specific change/bug fix?

**Self-Assessment Note:** This PR addresses: `[JIRA/ISSUE_NUMBER]` and relates to: `[Module Name]`.
```