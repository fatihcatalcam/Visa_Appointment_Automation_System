# Comp-Bot

**Distributed Browser Automation Platform**

Comp-Bot is a modular browser automation platform designed to monitor visa appointment availability and manage automated workflows through a web-based control system.

The project combines browser automation, backend APIs, infrastructure services, and monitoring tools into a unified architecture designed for long-running server environments.

---

# Overview

Comp-Bot is designed as an automation platform rather than a simple script.
The system integrates automation workers, a backend API, monitoring infrastructure, and a web management interface.

The platform can run continuously on a VPS and be managed through the dashboard.

---

# Features

* Automated monitoring of visa appointment availability
* Multi-session browser automation
* Headless backend architecture
* Web-based control dashboard
* Infrastructure monitoring
* Automated browser process management
* Designed for continuous operation on server environments

---

# System Architecture

The project follows a layered architecture separating automation logic, API services, infrastructure, and the frontend interface.

```
comp-bot
│
├ api/            FastAPI backend
├ bot/            browser automation engine
├ config/         configuration files
├ data/           runtime data (profiles, logs)
├ web_panel/      web management dashboard
│
├ docker-compose.yml
├ main.py
└ requirements.txt
```

---

# Tech Stack

### Backend

* Python
* FastAPI

### Automation

* Selenium
* undetected-chromedriver

### Frontend

* Vite
* JavaScript

### Infrastructure

* Docker
* Redis
* PostgreSQL

### Monitoring

* Prometheus
* Grafana

---

# How It Works

1. The automation engine launches browser sessions and monitors appointment availability.
2. The FastAPI backend manages automation processes and exposes API endpoints.
3. The web panel communicates with the API to control the system.
4. Redis and PostgreSQL handle runtime data and state management.
5. Prometheus collects system metrics and Grafana visualizes them.

---

# Getting Started

### Clone the repository

```
git clone https://github.com/fatihcatalcam/comp-bot
cd comp-bot
```

---

### Install dependencies

```
pip install -r requirements.txt
```

---

### Start infrastructure services

```
docker-compose up -d
```

This will start:

* PostgreSQL
* Redis
* Prometheus
* Grafana

---

### Run the backend server

```
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

---

### Start the web panel

```
cd web_panel
npm install
npm run build
```

---

# Monitoring

The project includes an observability stack:

* **Prometheus** collects runtime metrics
* **Grafana** provides dashboards for system monitoring

These tools help track automation activity, system health, and performance.

---

# Deployment

The system is designed to run on a **Windows VPS** for continuous automation.

Recommended deployment architecture:

```
Internet
   │
Reverse Proxy
   │
FastAPI Backend
   │
Automation Workers
   │
Docker Infrastructure
(PostgreSQL • Redis • Prometheus • Grafana)
```

---

# Project Goals

This project was built to explore:

* scalable automation systems
* API-driven automation platforms
* infrastructure monitoring
* long-running automation architectures

---

# Disclaimer

This project is intended for educational and research purposes related to automation system design.

---

# Author

Fatih Çatalçam
Computer Engineering Student
