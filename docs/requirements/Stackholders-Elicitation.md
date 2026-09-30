# Stakeholders & Elicitation

## 1. Overview

The **Mind Map Generator from Raw Ideas** involves multiple stakeholders who either directly use the system, develop and maintain it, test it, administer it, or provide external services required by the system.

Stakeholders were identified according to the project instructions, including **end users, administrators, and third parties who interact with or are affected by the system**.

The elicitation techniques were selected according to the nature of each stakeholder:

* **Broad/external stakeholders** → Surveys and Document Analysis
* **Internal/team-based stakeholders** → Joint Application Design (JAD) / Group Discussion and Brainstorming
* **System-level stakeholders** → Observation and Document Analysis

---

## 2. Stakeholder and Elicitation Analysis

| Stakeholder         | Description                                                                            | Elicitation Technique(s)                                             |
| ------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| **User**            | Primary user who provides ideas or reference material and creates and edits mind maps. | **Surveys, Document Analysis**                                       |
| **Admin**           | Manages users and platform-level settings.                                             | **Joint Application Design (JAD) / Group Discussion**                |
| **Developer**       | Develops and maintains the system, including the LLM pipeline.                         | **Joint Application Design (JAD) / Group Discussion, Brainstorming** |
| **QA / Tester**     | Tests the system and verifies its quality.                                             | **Joint Application Design (JAD) / Group Discussion**                |
| **LLM Service**     | AI service responsible for mind map generation and refinement.                         | **Observation**                                                      |
| **Third Party API** | External services such as Google OAuth, web search, arXiv, and export services.        | **Observation, Document Analysis**                                   |

---

## 3. Detailed Stakeholder Analysis

### 3.1 User

The **User** is the primary end user of the system. The user submits raw ideas or reference material, generates mind maps, edits the generated maps, and can collaborate on them.

#### Elicitation Techniques

**Survey**

Surveys are used to reach a broad and varied user base efficiently. They help capture different user expectations without requiring individual interviews with every user.

The survey is particularly useful for understanding:

* User expectations
* Desired features
* Input preferences
* Editing requirements
* Collaboration expectations
* Export requirements

**Document Analysis**

Comparable tools are reviewed to understand existing interaction and canvas-editing expectations.

The project considers:

* Miro
* Whimsical
* MindMeister
* Obsidian Canvas

This provides a baseline for expected mind-map interaction and canvas editing.


### 3.2 Admin

The **Admin** manages user accounts, oversees platform content, and configures system-level settings.

#### Elicitation Technique

**Joint Application Design (JAD) / Group Discussion**

Admin requirements are elicited through a structured group discussion. This allows the team to discuss administrative requirements and reach agreement efficiently.


### 3.3 Developer

The **Developer** builds and maintains the system, including the LLM integration and tool-augmented generation pipeline.

#### Elicitation Techniques

**Joint Application Design (JAD) / Group Discussion**

Developer requirements are identified through direct discussion with the project team to identify technical and development requirements.

**Brainstorming**

Brainstorming is used to identify additional architectural requirements.

The brainstorming process identified requirements related to:

* Web search
* arXiv
* Future tools


### 3.4 QA / Tester

The **QA / Tester** verifies that the system behaves correctly and meets quality expectations.

#### Elicitation Technique

**Joint Application Design (JAD) / Group Discussion**

Testing requirements and acceptance criteria are identified through collaborative discussion with the development team.


### 3.5 LLM Service

The **LLM Service** is the GenAI engine responsible for:

* Mind map generation
* Tool orchestration
* Node-level refinement

It is a system-level entity rather than a human stakeholder.

#### Elicitation Technique

**Observation**

Requirements are identified by observing the behaviour and outputs of the LLM Service.

The observation focuses on:

* Response quality
* Latency
* Failure modes
* Generated outputs
* System behaviour


### 3.6 Third Party API

Third-party APIs represent external services used by the system beyond the core LLM.

These include:

* Google OAuth
* Web search
* arXiv search
* Export/format conversion
* Future tools

#### Elicitation Techniques

**Observation**

The system's interaction with third-party services is observed to identify constraints such as:

* Rate limits
* Supported formats
* Availability
* Interaction behaviour

**Document Analysis**

Official documentation of each external service is reviewed to identify:

* Rate limits
* Authentication requirements
* Supported data formats
* API constraints

Examples include Google OAuth, search/arXiv APIs, and export libraries.

---


## 4. Stakeholder–Elicitation Mapping

```text
                         STAKEHOLDERS
                              │
             ┌────────────────┼────────────────┐
             │                │                │
          HUMAN           TECHNICAL         SYSTEM /
       STAKEHOLDERS       STAKEHOLDERS       EXTERNAL
             │                │                │
        ┌────┴────┐       ┌───┴────┐       ┌───┴────────┐
        │         │       │        │       │            │
      User      Admin  Developer  QA    LLM Service  Third Party
        │         │       │        │       │            │
      ┌─┴─┐       │      ┌┴─┐      │       │          ┌─┴─────┐
      │   │       │      │  │      │       │          │       │
   Survey Doc    JAD    JAD Brain-  JAD  Observation Observation
          Analysis          storming             │       +
                                                   Document
                                                   Analysis
```
