# Requirements Document

## Introduction

ReceiptIQ currently tracks a single user's receipts. This feature adds two capabilities.

First, family accounts. Separate member logins share a Family Group: each member signs up with an individual email and password and joins a shared family group. Receipts a member uploads stay attributed to that member but are visible family-wide. Every confirmed receipt carries a Member Label chosen from the family roster at review time, and the dashboard, explore, and chat can be filtered by which family member spent what and how much. Members only ever see their own family's data.

Second, a RAG chatbot. A hybrid assistant answers spending questions by combining tool-calling (for exact figures — budget, totals, and breakdowns via the existing stats, analysis, and insights functions) with retrieval-augmented generation (for fuzzy, item-level receipt searches over embeddings). Embeddings live in the single-file SQLite database (`receiptiq.db`) via sqlite-vec, so only the query to the cloud LLM leaves the machine. The LLM is Sarvam (already integrated for OCR) and/or Groq; OpenAI is not used. When both provider keys are configured, the user can choose the preferred provider per session and the system automatically falls back to the other if the chosen one fails.

## Glossary

- **ReceiptIQ**: The ReceiptIQ web application, comprising the FastAPI backend and the React frontend.
- **User**: A registered person who can sign in to ReceiptIQ.
- **Member**: A User who belongs to a Family Group.
- **Family Group**: A shared account grouping one or more Members who can view one another's receipts.
- **Roster**: The complete list of Members belonging to a Family Group.
- **Member Label**: An attribute on a Receipt identifying the Member who made the purchase.
- **Receipt**: A transaction record stored in the `receipts` table of `receiptiq.db`.
- **Pending Receipt**: A Receipt whose `status` is `pending`.
- **Confirmed Receipt**: A Receipt whose `status` is `confirmed`.
- **Chatbot**: The assistant in ReceiptIQ that answers spending questions using Tool Calls and RAG Retrieval.
- **Tool Call**: A structured invocation by the Chatbot of an analytics function to obtain exact figures.
- **Stats Function**: The `budget_status`, `compare_months`, and `insights` functions and the `/api/stats` endpoint.
- **Analysis Function**: The `analysis()` function and the `/api/analysis` endpoint.
- **RAG Retrieval**: A semantic search by the Chatbot over Receipt and item text using Embeddings from the Vector Store.
- **Vector Store**: The Embedding index stored inside `receiptiq.db` (sqlite-vec), used for RAG Retrieval.
- **Embedding**: A vector representation of a Receipt's text, used to populate the Vector Store.
- **LLM**: The cloud-hosted large language model used by the Chatbot to generate answers (Sarvam or Groq).
- **Chat Endpoint**: The backend API endpoint that receives chat messages and returns Chatbot answers.
- **Chat UI**: The frontend chat interface reached through the Chat tab.

## Requirements

### Requirement 1: Family Group Creation and Membership

**User Story:** As a user, I want to create a family group and let other members join with their own logins, so that we can share spending data while keeping individual identities.

#### Acceptance Criteria

1. WHEN a User signs up, THE ReceiptIQ SHALL create an individual account for that User.
2. WHEN a User creates a Family Group, THE ReceiptIQ SHALL add that User as the first Member of the Family Group.
3. WHEN a User submits a request to join an existing Family Group, THE ReceiptIQ SHALL add the User as a Member of that Family Group.
4. THE ReceiptIQ SHALL persist each Member's Family Group membership in `receiptiq.db`.
5. WHEN a Member requests the Roster, THE ReceiptIQ SHALL return the list of all Members in that Member's Family Group.
6. WHEN ReceiptIQ starts against a pre-existing `receiptiq.db`, THE ReceiptIQ SHALL migrate the schema to add Family Group and Member Label structures without discarding existing receipts, items, or users.

### Requirement 2: Member Attribution on Receipts

**User Story:** As a member, I want to tag each receipt with the family member who made the purchase, so that spending can be broken down by member.

#### Acceptance Criteria

1. WHILE a Member is reviewing a Pending Receipt, THE ReceiptIQ SHALL present the Roster for Member selection.
2. WHEN a Member confirms a Receipt, THE ReceiptIQ SHALL store the selected Member Label on that Receipt.
3. IF a Receipt is confirmed without a Member Label selected, THEN THE ReceiptIQ SHALL assign the Member Label of the confirming Member.
4. THE ReceiptIQ SHALL store a Member Label on every Confirmed Receipt.

### Requirement 3: Member-Filtered Data Access and Family-Wide Visibility

**User Story:** As a member, I want to see all my family's receipts and filter spending by member, so that I can understand who spent what.

#### Acceptance Criteria

1. WHEN a Member lists Receipts, THE ReceiptIQ SHALL return every Receipt belonging to the Member's Family Group, regardless of which Member uploaded it.
2. WHEN a Member requests stats or analysis filtered by a Member, THE ReceiptIQ SHALL include only Receipts carrying that Member's Label.
3. WHEN a Member requests stats or analysis with no Member filter, THE ReceiptIQ SHALL include all Receipts in the Member's Family Group.
4. THE ReceiptIQ SHALL scope all Receipt, image, Stats Function, and Analysis Function data to the requesting Member's Family Group.
5. IF a Member requests data belonging to a different Family Group, THEN THE ReceiptIQ SHALL return no records from that Family Group.

### Requirement 4: Chatbot Tool Calling for Exact Figures

**User Story:** As a user, I want to ask the chatbot about my spending and get answers backed by exact figures from my receipts, so that I can trust the numbers.

#### Acceptance Criteria

1. WHEN a Member submits a spending question that requires exact figures, THE Chatbot SHALL invoke one or more Tool Calls against the Stats Function or Analysis Function.
2. WHEN the question names a specific family Member, THE Chatbot SHALL apply that Member as the filter for Tool Calls.
3. WHERE the question refers to the asking Member's own spending, THE Chatbot SHALL filter Tool Calls to the logged-in Member.
4. WHERE the question refers to the whole Family Group, THE Chatbot SHALL apply no Member filter to Tool Calls.
5. WHEN a Member asks a budget question, THE Chatbot SHALL answer using the `budget_status` function computed over the Receipts in the current scope.
6. THE Chatbot SHALL base every numerical figure in an answer on Tool Call results, not on LLM-generated estimates.

### Requirement 5: Chatbot RAG Retrieval for Fuzzy Search

**User Story:** As a user, I want to ask fuzzy questions about specific receipts or items, so that I can find past purchases even when I do not remember exact details.

#### Acceptance Criteria

1. WHEN a Member submits a spending question that requires fuzzy or item-level receipt search, THE Chatbot SHALL perform RAG Retrieval against the Vector Store.
2. THE Chatbot SHALL scope RAG Retrieval to the Member's Family Group.
3. WHEN RAG Retrieval returns matching Receipts, THE Chatbot SHALL use those Receipts to compose the answer.

### Requirement 6: Vector Store and Embeddings

**User Story:** As the system, I want receipts and items indexed as embeddings, so that the chatbot can search them semantically.

#### Acceptance Criteria

1. THE ReceiptIQ SHALL store Receipt and item Embeddings in a Vector Store inside `receiptiq.db`.
2. WHEN a Receipt is confirmed, THE ReceiptIQ SHALL generate and store an Embedding derived from that Receipt's merchant, items, and raw text.
3. WHEN a Confirmed Receipt is edited, THE ReceiptIQ SHALL regenerate the Embedding for that Receipt.
4. WHEN a Receipt is deleted, THE ReceiptIQ SHALL remove the Embedding for that Receipt.
5. THE Vector Store SHALL index each Embedding under the Family Group of the Embedding's Receipt so that retrieval can be scoped.

### Requirement 7: LLM Provider Integration

**User Story:** As the system, I want to use a cloud LLM for answer generation with the ability to choose the provider and automatic failover, so that I do not need to host models locally and the chatbot remains available when one provider is down.

#### Acceptance Criteria

1. THE Chatbot SHALL support both Sarvam and Groq as LLM providers simultaneously when both API keys are configured.
2. WHERE both a Sarvam API key and a Groq API key are configured, THE Chat UI SHALL present a provider selector so the Member can choose which provider to use.
3. WHERE only one API key is configured, THE Chatbot SHALL use that provider with no selector shown and no fallback available.
4. WHEN a Member (or the system default) selects a preferred provider, THE Chatbot SHALL attempt to use that provider first.
5. IF the selected provider fails (API error, rate limit, or timeout), THEN THE Chatbot SHALL automatically retry the request using the other configured provider before returning an error.
6. IF both providers fail, THEN THE Chatbot SHALL return an error stating that all LLM providers are unavailable.
7. IF neither a Sarvam nor a Groq API key is configured, THEN THE Chatbot SHALL return an error stating that no LLM provider is available.

### Requirement 8: Chat Endpoint and Authentication

**User Story:** As a user, I want to send a question and receive an answer over the API, so that the chatbot integrates with the app.

#### Acceptance Criteria

1. WHEN a Member sends a chat message to the Chat Endpoint, THE ReceiptIQ SHALL authenticate the Member via the existing session.
2. WHEN a chat request is authenticated, THE ReceiptIQ SHALL return the Chatbot answer scoped to the Member's Family Group.
3. IF the chat request is unauthenticated, THEN THE ReceiptIQ SHALL reject the request with HTTP status 401.
4. THE ReceiptIQ SHALL include the Member's identity and Family Group scope in every chat answer.

### Requirement 9: Chat UI

**User Story:** As a user, I want a chat interface in the app, so that I can ask spending questions without leaving ReceiptIQ.

#### Acceptance Criteria

1. THE ReceiptIQ SHALL provide a Chat tab in the frontend navigation.
2. WHEN a Member opens the Chat tab, THE Chat UI SHALL display a chat input and the conversation of messages and answers for the current browser session.
3. WHEN a Member submits a message, THE Chat UI SHALL send the message to the Chat Endpoint and display the returned answer.

### Requirement 10: Member Filter UI

**User Story:** As a member, I want to filter the dashboard, explore, and chat by family member, so that I can see who spent what.

#### Acceptance Criteria

1. WHILE a Member views the Dashboard, Explore, or Chat tab, THE ReceiptIQ SHALL expose a Member filter populated from the Roster.
2. WHEN the logged-in Member opens a filtered view, THE ReceiptIQ SHALL default the Member filter to that logged-in Member.
3. WHEN a Member selects a different Member in the filter, THE ReceiptIQ SHALL refresh the view to show the selected Member's data.
4. WHERE no Member is selected in the filter, THE ReceiptIQ SHALL show the whole Family Group's data.

### Requirement 11: Answer Grounding and Refusal

**User Story:** As a user, I want chatbot answers grounded in my actual data, so that I can trust them.

#### Acceptance Criteria

1. WHEN the Chatbot composes an answer from specific Receipts, THE Chatbot SHALL reference those Receipts in the answer.
2. IF neither Tool Calls nor RAG Retrieval returns supporting data, THEN THE Chatbot SHALL state that no information is available to answer the question.
