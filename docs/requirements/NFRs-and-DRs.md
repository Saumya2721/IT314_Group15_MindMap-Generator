# Mind Map Generator — Non-Functional Requirements & Domain Requirements

## 1. Non-Functional Requirements (NFRs)

These requirements define the quality attributes, operational standards, and constraints of the mind map generator system.

### 1.1 Performance

**NFR-01: Generation Latency (Quick Summary)**
For text inputs equivalent to a 20-page document (approximately 10,000 words), the system shall generate a "high-level overview" or "balanced summary" mind map in under 30 seconds for 95% of user requests.

**NFR-02: Generation Latency (Detailed Analysis)**
For text inputs equivalent to a 20-page document, the system shall generate a "very detailed breakdown" mind map in under 180 seconds (3 minutes).

**NFR-03: UI Responsiveness**
All client-side UI interactions, such as dragging nodes, applying styles, opening menus, and panning the canvas, shall register and complete within 200 milliseconds to ensure a fluid user experience.

**NFR-04: Concurrent Users**
The production system shall be architected to support a minimum of 500 concurrent users performing typical actions such as generating maps, editing, and saving, with server-side response times remaining below 2 seconds.

### 1.2 Reliability & Availability

**NFR-05: System Availability**
The application shall achieve a minimum of 99.5% uptime, measured monthly. This calculation excludes pre-announced scheduled maintenance windows, which shall not exceed 4 hours per month.

**NFR-06: Data Integrity**
All save, load, and auto-save operations must complete without data loss or corruption. The system shall achieve a success rate of 99.99% for these critical data persistence operations.

**NFR-07: Graceful Error Handling**
The system must handle foreseeable errors gracefully, preventing application crashes or data loss. In the event of an unrecoverable error, the system shall provide the user with clear information and a support link.

### 1.3 Security & Privacy

**NFR-08: Secure Authentication**
The system shall enforce secure user authentication. User passwords must be salted and hashed using a modern, strong cryptographic algorithm such as Argon2.

**NFR-09: Data Encryption in Transit**
All communication between the user's client and the system's servers must be encrypted using Transport Layer Security (TLS) version 1.2 or higher.

**NFR-10: Data Encryption at Rest**
Any user-generated content, including mind map data and personal information, stored on the system's servers must be encrypted at rest using industry-standard encryption such as AES-256.

**NFR-11: User Data Control**
The system shall provide users with a clear and explicit choice to store their mind map data either on the cloud or exclusively on their local device. This choice should be available on a per-map basis.

**NFR-12: GDPR Compliance**
The system shall be designed and operated in compliance with the General Data Protection Regulation (GDPR). This includes providing users with the ability to access, export, and request deletion of their personal data.

### 1.4 Usability & Accessibility

**NFR-13: Learnability**
A new user, without prior training or documentation, must be able to successfully perform the core workflow of generating a mind map from text, making a basic edit such as renaming a node, and saving the map within 5 minutes of their first interaction with the application.

**NFR-14: Platform Support**
The application must be fully functional and render correctly on the latest two stable versions of Google Chrome (Desktop), Mozilla Firefox (Desktop), Safari (Desktop & iOS), Microsoft Edge (Desktop), Android, and dedicated desktop applications for Windows and macOS.

**NFR-15: Accessibility Compliance**
The entire application shall conform to the Web Content Accessibility Guidelines (WCAG) 2.1 Level AA, at a minimum.

**NFR-16: Contrast Ratio**
The contrast ratio between all text and its background shall be at least 4.5:1 to ensure readability for users with low vision.

**NFR-17: Plain Language**
All user-facing language, both within the application interface and in the documentation, shall be written in simple, clear prose, avoiding jargon and complex terminology to support cognitive accessibility.

### 1.5 Maintainability & Extensibility

**NFR-18: Modular Architecture for Future Features**
The software architecture must be modular to facilitate the addition of future subscription-based premium features, such as advanced collaboration tools and unlimited private maps, without requiring significant refactoring of the core application.

**NFR-19: DSL Extensibility**
The parser and interpreter for the Domain-Specific Language (DSL) must be designed to be extensible, allowing straightforward addition of new commands, attributes, and node types in future software updates.

---

## 2. Domain Requirements (DRs)

These requirements describe domain-specific rules and constraints derived from the nature of the mind map generator and its NFRs.

### 2.1 Mind Map Structure & Representation

**DR-01: Graph-Based Representation**
Every mind map shall represent information using nodes for concepts or ideas and edges for relationships between those nodes.

**DR-02: Single Root Concept**
Each mind map shall contain exactly one central/root node from which the remaining concepts are organized into branches.

**DR-03: Unique Node Identification**
Every node within a mind map shall have a unique identifier so that it can be referenced and updated without ambiguity.

**DR-04: Relationship Information**
Connections between nodes shall preserve sufficient information to identify and describe the relationship between the connected concepts.

### 2.2 Semantic Integrity & AI Processing

**DR-05: Meaning Preservation**
The transformation from user input to generated nodes and visual representation shall preserve the original meaning of the user's information.

**DR-06: Domain Neutrality**
The system shall be capable of processing concepts from different subject areas without assuming a fixed domain such as education, business, or medicine.

**DR-07: Multiple Levels of Abstraction**
The system shall support representation of information at different levels of detail, corresponding to overview, balanced, and detailed forms of a mind map.

**DR-08: Human-Readable Representation**
Generated mind maps shall remain understandable and logically organized even when the input contains a large amount of information.

### 2.3 Data Ownership, Privacy & Security

**DR-09: Single Ownership**
Each mind map shall have one identifiable owner who is responsible for the map and its associated data.

**DR-10: User Data Ownership**
Users shall retain control over their generated mind map data, including the ability to access, export, and delete their information.

**DR-11: Data Processing Privacy**
User-generated content shall only be processed and stored according to the user's selected storage and privacy preferences.

**DR-12: Secure Data Handling**
Credentials, personal information, and mind map content shall be handled in accordance with the system's security and encryption requirements.

### 2.4 Accessibility & Usability

**DR-13: Accessible Interaction**
All essential mind map operations shall remain usable by users with accessibility needs, including users who rely on keyboard navigation or assistive technologies.

**DR-14: Readable Visual Representation**
Mind maps and interface elements shall use clear typography, sufficient contrast, and appropriate spacing so that information remains readable.

**DR-15: Simple User Communication**
System messages, errors, instructions, and documentation shall communicate information in clear and understandable language.

### 2.5 Scalability & System Constraints

**DR-16: Scalable Mind Map Structure**
The mind map structure shall support growth in the number of nodes without breaking the logical relationships or making the map unusable.

**DR-17: Consistent Data Persistence**
A mind map shall retain its valid structure and content when it is saved, loaded, or automatically saved.

**DR-18: Platform-Independent Behavior**
The core behavior and meaning of a mind map shall remain consistent across the supported browsers, mobile platforms, and desktop applications.

### 2.6 Extensibility

**DR-19: Future Feature Integration**
The system domain model shall allow future capabilities, including premium or advanced collaboration features, to be integrated without changing the fundamental structure of existing mind maps.

---

## 3. Non-Functional & Domain Requirement – Stakeholder Mapping

The non-functional requirements (Section 1) and domain requirements (Section 2) can be mapped to their primary stakeholders as follows:

| Stakeholder | Non-Functional Requirements | Domain Requirements |
|---|---|---|
| User | NFR-01 to NFR-07, NFR-11 to NFR-17 | DR-07 to DR-11, DR-13 to DR-15 |
| Admin | NFR-12 | DR-09, DR-11 |
| Developer | NFR-04 to NFR-10, NFR-14 to NFR-16, NFR-18, NFR-19 | DR-01 to DR-04, DR-12 to DR-14, DR-16 to DR-19 |
| QA / Tester | — | DR-18 |
| LLM Service | NFR-01, NFR-02 | DR-01, DR-02, DR-04 to DR-08 |
| Third Party API | NFR-09 | DR-12 |

---

