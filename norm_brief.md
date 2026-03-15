# Hospitality AI Orchestration Platform — Build Brief


## 1. Project overview


Build a new standalone SaaS application from scratch that allows hospitality operators to use natural language to ask software agents to perform operational tasks across multiple business systems.


The product will act as an independent orchestration platform. It will interpret user requests, route them to specialist agents, create structured draft actions, request approval where needed, and then execute approved actions through integrations with external platforms.


The initial prototype goal is to demonstrate one narrow but compelling workflow:


**A user can type a natural-language stock ordering request and the system can interpret it, create a draft order, present it clearly for approval, and submit it.**


Example:


> “Order 3 cases of Jim Beam for La Zeppa.”


This should feel like the beginning of a broader multi-agent hospitality operations platform that could later support rostering, onboarding, compliance, reporting, and other operational workflows.


---


## 2. Core vision


Create an **AI operations orchestration platform for hospitality**.


The user should not need to know which back-end system performs the task. They should simply ask for what they want in natural language. The platform should:


- understand the request
- identify the intent
- resolve entities like venue, product, supplier, employee, or date
- create a structured plan
- call internal tools
- produce a draft action
- require approval where appropriate
- execute through integrations
- record everything in an audit trail


The product should feel like an intelligent operations assistant for hospitality groups.


---


## 3. Why this is being built separately


This product is intentionally being built a new product sitting on top of other products rather than building into existing products.


Reasons:


- avoid being constrained by existing architecture
- avoid internal resistance from the current dev team
- move faster with a greenfields prototype
- prove the concept independently
- create a neutral orchestration layer that can work across multiple systems
- maintain optionality around future product direction and commercialisation


This means the new system should be designed as a standalone application with its own:


- frontend
- backend
- database
- authentication
- task model
- approval model
- integration layer
- audit trail


LoadedHub will be our first integration as just one connector among several.


---


## 4. Product goals


### Short-term goal


Within approximately one week, build a credible prototype that demonstrates stock ordering via natural language.


### Medium-term goal


Expand into a multi-agent operations platform capable of handling multiple hospitality workflows such as:


- stock ordering
- roster drafting
- onboarding new employees
- shift changes
- supplier and product queries
- compliance and checklist actions
- reporting and operational summaries


### Long-term goal


Create a scalable multi-tenant platform that becomes the AI operations layer across hospitality businesses and their software stack.


---


## 5. Prototype scope


The first version must stay narrow.


### In scope for prototype


- one standalone web app
- one natural-language chat/task interface
- one supervisor/orchestrator agent
- one procurement agent
- one ordering workflow
- one or two suppliers max
- one or more venues
- product and alias resolution
- draft purchase order creation
- approval flow
- final order submission to either:
  - a real supplier endpoint if practical, or
  - a mocked submission path that convincingly demonstrates the action
- audit logging
- task status tracking


### Out of scope for prototype


- rostering
- HR onboarding
- multi-step cross-domain orchestration
- deep forecasting
- complex permissions matrix
- advanced supplier price logic
- substitution logic
- partial delivery management
- invoice reconciliation
- browser automation
- enterprise-grade workflow durability
- mobile app
- polished design system


The prototype should focus on proving the workflow and user experience, not solving every edge case.


---


## 6. Key user story


### Primary demo user story


A hospitality manager can type a request such as:


> “Order 3 cases of Jim Beam for La Zeppa.”


The system should:


1. understand that this is a procurement/order intent
2. resolve “Jim Beam” to the correct supplier product
3. resolve “La Zeppa” to the correct venue
4. understand that “3 cases” is the requested quantity
5. identify the relevant supplier and SKU
6. create a structured draft order
7. present the draft clearly in the UI
8. require approval before submission
9. submit the order when approved
10. record the result and show confirmation


### Additional example prompts for the prototype


- “Please order 2 cases of Corona for Mr Murdoch’s”
- “Get me 1 case of Jim Beam for La Zeppa for tomorrow”
- “Can you place a bourbon order for Freeman & Grey”
- “Order 3 cases of Jim Beam”
  - if venue is missing, the system should ask or prompt for a venue


---


## 7. Product principles


### 1. Natural language starts the workflow
The user speaks in plain English, not system commands.


### 2. Chat triggers structured actions
The product should not remain just a chat stream. Natural language should produce structured task cards and action records.


### 3. Draft first, execute second
The system should create a draft action first for potentially meaningful actions.


### 4. Agents reason, software executes
The LLM should interpret and plan. Deterministic internal services should own data writes and integrations.


### 5. Internal tools only
Agents should never call external systems directly. They should call internal platform tools.


### 6. Audit everything
Every action should be traceable from request to interpretation to execution.


### 7. Keep the first build narrow
A small working prototype is more valuable than a broad unfinished platform.


---


## 8. Recommended architecture


### High-level architecture


**Frontend web app**  
→ **Backend API**  
→ **Agent orchestration layer**  
→ **Internal tools/capability layer**  
→ **Connector/integration layer**  
→ **Supplier or mocked external system**


### Recommended prototype tech stack


#### Frontend
- Next.js
- React
- simple chat/task UI
- structured approval cards


#### Backend
- Python FastAPI
- REST endpoints for task/message/approval flows


#### Agent layer
- CrewAI for fast prototyping of:
  - supervisor agent
  - procurement agent


#### Database
- Postgres or Supabase Postgres


#### Hosting
- GitHub Codespaces for development
- later deployable to Vercel + Railway/Render/Azure


### Why this stack


- fast to scaffold
- good for a one-week prototype
- easy to demonstrate
- Python is convenient for agent tooling
- CrewAI is acceptable for prototyping multi-agent workflows quickly
- architecture can later evolve without rewriting core business contracts


---


## 9. Agent architecture


The prototype should use a simple hub-and-spoke model.


### Supervisor agent
Responsibilities:
- receive the user request
- classify the intent
- determine whether procurement agent should handle it
- identify missing context
- produce a structured task request


### Procurement agent
Responsibilities:
- resolve product names
- resolve suppliers and SKUs
- interpret quantities
- create a draft order proposal
- explain assumptions if needed


### Important rule
Agents should not write directly to third-party systems. Agents can only call internal tools exposed by the backend.


---


## 10. Internal tools/capability layer


Create internal service functions that the agents can call.


### Example tools for prototype


- `resolve_venue(name)`
- `resolve_product_name(query, venue_id)`
- `get_supplier_product(product_id)`
- `create_order_draft(venue_id, supplier_id, lines)`
- `check_order_policy(draft_id)`
- `submit_order(draft_id)`
- `get_task_status(task_id)`


These functions should be deterministic and live in the application code, not the agent prompt logic.


---


## 11. Workflow design for prototype


### Basic workflow


1. User enters request
2. Request saved to database
3. Supervisor agent processes message
4. Supervisor routes to procurement agent
5. Procurement agent calls internal tools
6. Draft order created
7. Draft returned to UI as a structured task card
8. User approves or rejects
9. If approved, backend calls submit action
10. Submission result saved and shown in UI


### States to support


- `received`
- `processing`
- `draft_ready`
- `awaiting_approval`
- `approved`
- `submitting`
- `submitted`
- `failed`
- `rejected`


---


## 12. UX requirements


The UI should be simple and functional, not over-designed.


### Core screens


#### Main task/chat screen
Should include:
- venue selector or current venue context
- conversation thread
- input box
- structured task card area


#### Task card should show
- intent type
- venue
- supplier
- product line(s)
- quantity
- estimated cost if available
- assumptions
- status
- approve button
- reject button


#### Confirmation view
After submit, show:
- order submitted
- supplier
- time submitted
- draft/order reference
- success or failure status


### UX principle
The experience should feel like:
- natural language in
- structured action out


Not just a chatbot response.


---


## 13. Data model


The prototype needs a lightweight but proper schema.


### Core tables


#### `venues`
- id
- name
- code
- status


#### `suppliers`
- id
- name
- status


#### `products`
- id
- supplier_id
- sku
- display_name
- pack_size
- default_unit
- default_cost
- status


#### `product_aliases`
- id
- product_id
- alias


#### `tasks`
- id
- type
- status
- user_id
- venue_id
- raw_prompt
- parsed_payload_json
- result_payload_json
- created_at
- updated_at


#### `messages`
- id
- task_id
- role
- content
- created_at


#### `order_drafts`
- id
- task_id
- venue_id
- supplier_id
- status
- estimated_total
- created_at
- updated_at


#### `order_draft_lines`
- id
- order_draft_id
- product_id
- quantity_cases
- quantity_units
- estimated_cost


#### `approvals`
- id
- task_id
- status
- approved_by
- approved_at
- rejected_by
- rejected_at


#### `integration_runs`
- id
- task_id
- connector_name
- request_payload_json
- response_payload_json
- status
- created_at


---


## 14. API requirements


### Suggested endpoints


#### Task/message endpoints
- `POST /api/messages`
- `GET /api/tasks/:id`
- `GET /api/tasks`


#### Approval endpoints
- `POST /api/tasks/:id/approve`
- `POST /api/tasks/:id/reject`


#### Draft/order endpoints
- `GET /api/order-drafts/:id`
- `POST /api/order-drafts/:id/submit`


#### Reference data endpoints
- `GET /api/venues`
- `GET /api/products/search`
- `GET /api/suppliers`


These can remain simple for the prototype.


---


## 15. Initial integrations


### Prototype integration strategy
Use a simple adapter pattern.


### Phase 1
- internal/mock supplier submission
- simulate order submission but persist an integration run record


### Optional Phase 1.5
- send order via email/webhook to prove real-world action


### Later
- direct supplier integrations
- LoadedHub connector
- Deputy connector
- BambooHR connector
- Tanda connector


The prototype should be designed so connectors can be swapped or added later.


---


## 16. Non-functional requirements for prototype


### Must have
- clean repo structure
- stable local development in Codespaces
- environment variable configuration
- seeded test data
- repeatable setup
- logs for debugging
- basic error handling
- task status persistence


### Nice to have
- simple auth
- usage tracing
- prompt/version tracking
- retry button for failed submission


### Not necessary yet
- full enterprise security posture
- fine-grained RBAC
- full observability stack
- advanced queuing infrastructure


---


## 17. Suggested repo structure


```text
hospitality-ai/
  apps/
    web/
    api/
  packages/
    agents/
    core/
    db/
    ui/
    connectors/
  docs/
  scripts/
```


### Suggested interpretation


#### `apps/web`
Next.js frontend


#### `apps/api`
FastAPI backend


#### `packages/agents`
CrewAI agents, prompts, tools registration


#### `packages/core`
business services and workflow logic


#### `packages/db`
schema, migrations, seed data


#### `packages/connectors`
mock supplier connector and later real integrations


This can be simplified if needed, but the structure should anticipate growth.


---


## 18. Delivery phases


### Phase 0 — setup
- create new repo in Codespace
- scaffold frontend and backend
- connect database
- create seed scripts
- confirm local run flow


### Phase 1 — reference data and tools
- model venues, suppliers, products, aliases
- build seed data
- build internal lookup functions
- build draft order creation service


### Phase 2 — agents
- implement supervisor agent
- implement procurement agent
- connect tool layer
- test prompts against realistic user requests


### Phase 3 — UI and workflow
- build chat/task screen
- render draft order cards
- build approve/reject interactions
- show task state changes


### Phase 4 — submission
- implement mock or real order submission path
- record integration runs
- show success/failure in UI


### Phase 5 — polish
- refine prompts
- improve error states
- clean up demo data
- prepare demo scenarios


---


## 19. Demo success criteria


The prototype is successful if it can reliably demonstrate the following end-to-end flow:


1. user can connect to LoadedHub through OAuth and save the connection
2. user enters a natural-language order request
3. system interprets the request correctly
4. correct venue and product are resolved
5. draft order is created and displayed clearly
6. user approves the order
7. system submits the order through a mock or real connector
8. system records and displays the outcome


Bonus success criteria:
- handles one or two ambiguous prompts gracefully
- asks for missing venue or product clarification when required
- shows a trustworthy audit trail


---


## 20. Future roadmap after prototype


After the ordering prototype, likely next priorities could include:


### Domain expansion
- rostering agent
- employee onboarding agent
- reporting agent
- checklist/compliance agent


### Platform expansion
- approvals framework
- multi-tenant architecture hardening
- connector framework expansion
- authentication and permissions
- durable workflow engine
- event-driven processing


### Potential long-term architecture evolution
- keep or reduce CrewAI depending on fit
- possibly move orchestration toward LangGraph or a more durable orchestration layer later
- introduce Temporal or equivalent workflow engine once workflows become longer-running and more critical


The system should be built so the agent framework is swappable later.


---


## 21. Constraints and build philosophy


### Constraints
- prototype speed matters
- build from scratch in a new Codespace
- keep architecture clean but lightweight
- optimise for demo value, not enterprise completeness


### Philosophy
- narrow scope
- strong demo story
- clear system boundaries
- internal tools before external integrations
- draft before execute
- chat plus structure, not chat only


---


## 22. Final summary


Build a standalone hospitality AI orchestration platform from scratch.


The first release should focus on one excellent workflow: **natural-language stock ordering**.


A user should be able to type an order request in plain language, have the system interpret it, create a draft order, ask for approval, and submit it.


The prototype should be architected in a way that feels like the beginning of a much broader multi-agent hospitality platform, while remaining narrow enough to build quickly and demonstrate convincingly.





