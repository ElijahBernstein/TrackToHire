// frontend/app.js
// UPDATED: Uses JWT authentication instead of user_id query parameters.

const API_URL = "https://api.tracktohire.app/api/v1/applications";
const AUTH_URL = "https://api.tracktohire.app/api/v1/auth";
const GMAIL_URL = "https://api.tracktohire.app/api/v1/gmail";

// Stores the JWT for the currently logged-in user.
let accessToken = null;
let isLoginMode = true;

const GMAIL_SUGGESTION_POLL_INTERVAL_MS = 10000;

let gmailSuggestionPollId = null;
let gmailSuggestionFetchInProgress = false;
let gmailConnectionActive = false;
let currentGmailSuggestions = [];
let currentDashboardView = "jobs";

const APPLICATION_STATUSES = [
    "Applied",
    "Assessment",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Unknown"
];

let allApplications = [];
let currentApplicationsPage = 1;


// --- PAGE ELEMENTS ---

const authTitle = document.getElementById("authTitle");
const authEyebrow = document.getElementById("authEyebrow");
const authDescription = document.getElementById("authDescription");
const authSubmitBtn = document.getElementById("authSubmitBtn");
const authForm = document.getElementById("authForm");
const authToggle = document.querySelector(".auth-toggle");
const authEmail = document.getElementById("authEmail");
const authPassword = document.getElementById("authPassword");
const authConfirmPassword = document.getElementById(
    "authConfirmPassword"
);
const confirmPasswordGroup = document.getElementById(
    "confirmPasswordGroup"
);
const loginOptions = document.getElementById("loginOptions");
const forgotPasswordBtn = document.getElementById(
    "forgotPasswordBtn"
);
const authMessage = document.getElementById("authMessage");
const passwordToggleButtons = document.querySelectorAll(
    ".password-toggle"
);

const landingScreen = document.getElementById("landingScreen");
const dashboardScreen = document.getElementById("dashboardScreen");

const logoutBtn = document.getElementById("logoutBtn");
const jobForm = document.getElementById("jobForm");
const jobsTableBody = document.getElementById("jobsTableBody");
const jobCount = document.getElementById("jobCount");
const visibleJobCount = document.getElementById("visibleJobCount");
const jobSearch = document.getElementById("jobSearch");
const statusFilter = document.getElementById("statusFilter");
const jobSort = document.getElementById("jobSort");
const clearFiltersBtn = document.getElementById("clearFiltersBtn");
const pageSize = document.getElementById("pageSize");
const previousPageBtn = document.getElementById("previousPageBtn");
const nextPageBtn = document.getElementById("nextPageBtn");
const pageSummary = document.getElementById("pageSummary");
const statCards = document.querySelectorAll(".stat-card");
const dashboardTabs = document.querySelectorAll(".dashboard-tab");
const dashboardPanels = document.querySelectorAll(".workspace-panel");
const dashboardSectionSelect = document.getElementById(
    "dashboardSectionSelect"
);
const tabSuggestionCount = document.getElementById(
    "tabSuggestionCount"
);

const statusCounterElements = {
    Applied: document.getElementById("appliedCount"),
    Assessment: document.getElementById("assessmentCount"),
    Interview: document.getElementById("interviewCount"),
    Offer: document.getElementById("offerCount"),
    Rejected: document.getElementById("rejectedCount"),
    Withdrawn: document.getElementById("withdrawnCount"),
    Unknown: document.getElementById("unknownCount")
};
const emptyJobsMessage = document.getElementById(
    "emptyJobsMessage"
);

// --- GMAIL PAGE ELEMENTS ---

const gmailConnectionBadge = document.getElementById(
    "gmailConnectionBadge"
);

const gmailDisconnectedView = document.getElementById(
    "gmailDisconnectedView"
);

const gmailConnectedView = document.getElementById(
    "gmailConnectedView"
);

const connectedGmailEmail = document.getElementById(
    "connectedGmailEmail"
);

const connectGmailBtn = document.getElementById(
    "connectGmailBtn"
);

const disconnectGmailBtn = document.getElementById(
    "disconnectGmailBtn"
);

const syncGmailBtn = document.getElementById(
    "syncGmailBtn"
);

const startGmailMonitoringBtn = document.getElementById(
    "startGmailMonitoringBtn"
);

const gmailMonitoringBadge = document.getElementById(
    "gmailMonitoringBadge"
);

const gmailMonitoringDetails = document.getElementById(
    "gmailMonitoringDetails"
);

const gmailSyncMessage = document.getElementById(
    "gmailSyncMessage"
);

const gmailSuggestionsArea = document.getElementById(
    "gmailSuggestionsArea"
);

const gmailSuggestionCount = document.getElementById(
    "gmailSuggestionCount"
);

const gmailSuggestionsList = document.getElementById(
    "gmailSuggestionsList"
);

const emptyGmailSuggestions = document.getElementById(
    "emptyGmailSuggestions"
);

// --- AUTHORIZATION HEADERS ---

function getAuthHeaders(includeJson = false) {
    const headers = {
        Authorization: `Bearer ${accessToken}`
    };

    if (includeJson) {
        headers["Content-Type"] = "application/json";
    }

    return headers;
}


// --- AUTH SCREEN MANAGEMENT ---

function hideAuthMessage() {
    authMessage.textContent = "";
    authMessage.classList.add("hidden");
    authMessage.classList.remove("error");
}


function showAuthMessage(message, { isError = false } = {}) {
    authMessage.textContent = message;
    authMessage.classList.remove("hidden");
    authMessage.classList.toggle("error", isError);
}


function updateAuthScreen() {
    hideAuthMessage();
    authConfirmPassword.setCustomValidity("");

    [authPassword, authConfirmPassword].forEach((passwordInput) => {
        passwordInput.type = "password";
    });

    passwordToggleButtons.forEach((toggleButton) => {
        toggleButton.setAttribute("aria-pressed", "false");
        toggleButton.setAttribute("aria-label", "Show password");
    });

    if (isLoginMode) {
        authEyebrow.innerText = "Welcome back";
        authTitle.innerText = "Log in to your account";
        authDescription.innerText =
            "Pick up where you left off in your job search.";
        authSubmitBtn.innerText = "Log in";

        confirmPasswordGroup.classList.add("hidden");
        authConfirmPassword.required = false;
        authConfirmPassword.value = "";
        loginOptions.classList.remove("hidden");
        authPassword.autocomplete = "current-password";

        authToggle.innerHTML = `
            Don't have an account?
            <button
                type="button"
                id="toggleAuthMode"
                class="text-button"
            >
                Create one
            </button>
        `;
    } else {
        authEyebrow.innerText = "Get organized";
        authTitle.innerText = "Create your account";
        authDescription.innerText =
            "Start tracking your applications in one clear workspace.";
        authSubmitBtn.innerText = "Create account";

        confirmPasswordGroup.classList.remove("hidden");
        authConfirmPassword.required = true;
        loginOptions.classList.add("hidden");
        authPassword.autocomplete = "new-password";

        authToggle.innerHTML = `
            Already have an account?
            <button
                type="button"
                id="toggleAuthMode"
                class="text-button"
            >
                Log in
            </button>
        `;
    }
}


async function switchToDashboard() {
    landingScreen.classList.remove("active");
    landingScreen.classList.add("hidden");

    dashboardScreen.classList.remove("hidden");
    dashboardScreen.classList.add("active");

    switchDashboardView("jobs");

    // Applications load first so Gmail suggestion selectors are complete.
    await fetchApplications();
    await fetchGmailConnectionStatus();
}


function switchToLogin() {
    // NEW: Clear the JWT when logging out.
    stopGmailSuggestionPolling();

    accessToken = null;
    isLoginMode = true;

    dashboardScreen.classList.remove("active");
    dashboardScreen.classList.add("hidden");

    landingScreen.classList.remove("hidden");
    landingScreen.classList.add("active");

    authForm.reset();
    jobForm.reset();

    jobsTableBody.innerHTML = "";
    allApplications = [];
    currentGmailSuggestions = [];
    currentApplicationsPage = 1;
    jobSearch.value = "";
    statusFilter.value = "";
    jobSort.value = "newest";
    updateApplicationCounters([]);

    emptyJobsMessage.classList.add("hidden");
    showDisconnectedGmailState();

    updateAuthScreen();
}


function switchDashboardView(viewName, { focusTab = false } = {}) {
    const nextView = viewName === "email" ? "email" : "jobs";
    currentDashboardView = nextView;

    dashboardPanels.forEach((panel) => {
        const isActive = panel.dataset.dashboardPanel === nextView;
        panel.classList.toggle("hidden", !isActive);
    });

    dashboardTabs.forEach((tab) => {
        const isActive = tab.dataset.dashboardView === nextView;
        tab.classList.toggle("active", isActive);
        tab.setAttribute("aria-selected", String(isActive));
        tab.tabIndex = isActive ? 0 : -1;

        if (isActive && focusTab) {
            tab.focus();
        }
    });

    dashboardSectionSelect.value = nextView;
}


// --- APPLICATION COUNTERS, FILTERS, AND PAGINATION ---

function updateApplicationCounters(applications) {
    const counts = Object.fromEntries(
        APPLICATION_STATUSES.map((status) => [status, 0])
    );

    applications.forEach((application) => {
        const status = APPLICATION_STATUSES.includes(application.status)
            ? application.status
            : "Unknown";

        counts[status] += 1;
    });

    jobCount.textContent = applications.length;

    APPLICATION_STATUSES.forEach((status) => {
        statusCounterElements[status].textContent = counts[status];
    });
}


function updateActiveStatCard() {
    statCards.forEach((card) => {
        card.classList.toggle(
            "active",
            card.dataset.statusFilter === statusFilter.value
        );
    });
}


function getFilteredApplications() {
    const searchTerm = jobSearch.value.trim().toLowerCase();
    const selectedStatus = statusFilter.value;

    const filtered = allApplications.filter((application) => {
        const status = application.status || "Applied";
        const matchesStatus =
            !selectedStatus || status === selectedStatus;

        const searchableText = [
            application.company_name,
            application.job_title,
            application.location,
            status
        ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

        return matchesStatus && searchableText.includes(searchTerm);
    });

    return filtered.sort((first, second) => {
        if (jobSort.value === "company") {
            return first.company_name.localeCompare(second.company_name);
        }

        const firstDate = new Date(first.date_applied || 0).getTime();
        const secondDate = new Date(second.date_applied || 0).getTime();

        return jobSort.value === "oldest"
            ? firstDate - secondDate
            : secondDate - firstDate;
    });
}


function renderApplications() {
    const filteredApplications = getFilteredApplications();
    const rowsPerPage = Number(pageSize.value);
    const totalPages = Math.max(
        1,
        Math.ceil(filteredApplications.length / rowsPerPage)
    );

    currentApplicationsPage = Math.min(
        Math.max(currentApplicationsPage, 1),
        totalPages
    );

    const startIndex = (currentApplicationsPage - 1) * rowsPerPage;
    const pageApplications = filteredApplications.slice(
        startIndex,
        startIndex + rowsPerPage
    );

    jobsTableBody.innerHTML = "";
    pageApplications.forEach((application) => {
        jobsTableBody.appendChild(createJobRow(application));
    });

    const hasApplications = allApplications.length > 0;
    const hasFilteredApplications = filteredApplications.length > 0;

    emptyJobsMessage.textContent = hasApplications
        ? "No applications match these filters."
        : "You have not added any applications yet.";

    emptyJobsMessage.classList.toggle(
        "hidden",
        hasFilteredApplications
    );

    document.getElementById("jobsTable").classList.toggle(
        "hidden",
        !hasFilteredApplications
    );

    visibleJobCount.textContent =
        `${filteredApplications.length} shown`;
    pageSummary.textContent =
        `Page ${currentApplicationsPage} of ${totalPages}`;

    previousPageBtn.disabled = currentApplicationsPage === 1;
    nextPageBtn.disabled = currentApplicationsPage === totalPages;

    updateActiveStatCard();
}

// --- GMAIL CONNECTION MANAGEMENT ---

function stopGmailSuggestionPolling() {
    if (gmailSuggestionPollId !== null) {
        window.clearInterval(gmailSuggestionPollId);
        gmailSuggestionPollId = null;
    }
}


function startGmailSuggestionPolling() {
    stopGmailSuggestionPolling();

    gmailSuggestionPollId = window.setInterval(() => {
        fetchGmailSuggestions({ silent: true });
    }, GMAIL_SUGGESTION_POLL_INTERVAL_MS);
}


function formatWatchExpiration(watchExpiration) {
    if (!watchExpiration) {
        return "No active Gmail watch expiration is available.";
    }

    const expirationDate = new Date(watchExpiration);

    if (Number.isNaN(expirationDate.getTime())) {
        return "The Gmail watch expiration could not be displayed.";
    }

    return `Active until ${expirationDate.toLocaleString()}.`;
}


function updateGmailMonitoringState(connectionData) {
    const monitoringActive = Boolean(
        connectionData.monitoring_active
    );

    gmailMonitoringBadge.classList.toggle(
        "active",
        monitoringActive
    );

    gmailMonitoringBadge.classList.toggle(
        "inactive",
        !monitoringActive
    );

    if (monitoringActive) {
        gmailMonitoringBadge.textContent =
            "Automatic monitoring active";

        gmailMonitoringDetails.textContent =
            `${formatWatchExpiration(
                connectionData.watch_expiration
            )} New suggestions refresh every 10 seconds.`;

        startGmailMonitoringBtn.textContent =
            "Disable Automatic Monitoring";
        startGmailMonitoringBtn.classList.add("disable-mode");

        startGmailSuggestionPolling();
    } else {
        gmailMonitoringBadge.textContent =
            "Automatic monitoring off";

        gmailMonitoringDetails.textContent =
            "Enable monitoring to receive new email suggestions " +
            "automatically.";

        startGmailMonitoringBtn.textContent =
            "Enable Automatic Monitoring";
        startGmailMonitoringBtn.classList.remove("disable-mode");

        stopGmailSuggestionPolling();
    }
}

function showDisconnectedGmailState() {
    gmailConnectionActive = false;
    stopGmailSuggestionPolling();

    gmailConnectionBadge.textContent = "Not Connected";
    gmailConnectionBadge.classList.remove("connected");
    gmailConnectionBadge.classList.add("disconnected");

    gmailDisconnectedView.classList.remove("hidden");
    gmailConnectedView.classList.add("hidden");
    gmailSuggestionsArea.classList.add("hidden");

    connectedGmailEmail.textContent = "";
    gmailMonitoringBadge.textContent =
        "Automatic monitoring off";
    gmailMonitoringBadge.classList.remove("active");
    gmailMonitoringBadge.classList.add("inactive");
    gmailMonitoringDetails.textContent =
        "Enable monitoring to receive new email suggestions " +
        "automatically.";
    startGmailMonitoringBtn.textContent =
        "Enable Automatic Monitoring";

    gmailSyncMessage.textContent = "";
    gmailSyncMessage.classList.add("hidden");

    gmailSuggestionsList.innerHTML = "";
    gmailSuggestionCount.textContent = "0";
    currentGmailSuggestions = [];
    tabSuggestionCount.textContent = "0";
    tabSuggestionCount.classList.add("hidden");
}


async function showConnectedGmailState(connectionData) {
    gmailConnectionActive = true;

    gmailConnectionBadge.textContent = "Connected";
    gmailConnectionBadge.classList.remove("disconnected");
    gmailConnectionBadge.classList.add("connected");

    gmailDisconnectedView.classList.add("hidden");
    gmailConnectedView.classList.remove("hidden");
    gmailSuggestionsArea.classList.remove("hidden");

    connectedGmailEmail.textContent = connectionData.gmail_email;

    updateGmailMonitoringState(connectionData);

    await fetchGmailSuggestions();
}


async function fetchGmailConnectionStatus() {
    if (!accessToken) {
        return;
    }

    try {
        const response = await fetch(
            `${GMAIL_URL}/status`,
            {
                method: "GET",
                headers: getAuthHeaders()
            }
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.detail ||
                "Could not check Gmail connection status."
            );
        }

        if (data.connected) {
            await showConnectedGmailState(data);
        } else {
            showDisconnectedGmailState();
        }
    } catch (error) {
        console.error(
            "Error checking Gmail connection:",
            error
        );

        showDisconnectedGmailState();
    }
}


async function connectGmail() {
    if (!accessToken) {
        switchToLogin();
        return;
    }

    connectGmailBtn.disabled = true;
    connectGmailBtn.textContent = "Opening Google...";

    try {
        const response = await fetch(
            `${GMAIL_URL}/connect`,
            {
                method: "GET",
                headers: getAuthHeaders()
            }
        );

        const data = await response.json();

        if (!response.ok) {
            alert(
                data.detail ||
                "Could not start Gmail authorization."
            );

            return;
        }

        window.open(
            data.authorization_url,
            "_blank",
            "noopener,noreferrer"
        );
    } catch (error) {
        console.error(
            "Error starting Gmail connection:",
            error
        );

        alert("Could not connect to the backend.");
    } finally {
        connectGmailBtn.disabled = false;
        connectGmailBtn.textContent = "Connect Gmail";
    }
}


async function startGmailMonitoring() {
    if (!accessToken) {
        switchToLogin();
        return;
    }

    startGmailMonitoringBtn.disabled = true;
    startGmailMonitoringBtn.textContent =
        "Starting Monitoring...";

    gmailSyncMessage.textContent =
        "Starting automatic Gmail monitoring...";
    gmailSyncMessage.classList.remove("hidden");

    try {
        const response = await fetch(
            `${GMAIL_URL}/monitoring/start`,
            {
                method: "POST",
                headers: getAuthHeaders()
            }
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            gmailSyncMessage.textContent =
                data.detail ||
                "Automatic Gmail monitoring could not be started.";

            return;
        }

        gmailSyncMessage.textContent = data.message;

        await fetchGmailConnectionStatus();
    } catch (error) {
        console.error(
            "Error starting Gmail monitoring:",
            error
        );

        gmailSyncMessage.textContent =
            "Could not connect to the backend.";
    } finally {
        startGmailMonitoringBtn.disabled = false;

        if (
            startGmailMonitoringBtn.textContent ===
            "Starting Monitoring..."
        ) {
            startGmailMonitoringBtn.textContent =
                gmailMonitoringBadge.classList.contains("active")
                    ? "Disable Automatic Monitoring"
                    : "Enable Automatic Monitoring";
        }
    }
}


async function disconnectGmail() {
    if (!accessToken) {
        switchToLogin();
        return;
    }

    const shouldDisconnect = window.confirm(
        "Disconnect Gmail from TrackToHire?"
    );

    if (!shouldDisconnect) {
        return;
    }

    disconnectGmailBtn.disabled = true;
    disconnectGmailBtn.textContent = "Disconnecting...";

    try {
        const response = await fetch(
            `${GMAIL_URL}/disconnect`,
            {
                method: "DELETE",
                headers: getAuthHeaders()
            }
        );

        const data = await response.json();

        if (!response.ok) {
            alert(
                data.detail ||
                "Could not disconnect Gmail."
            );

            return;
        }

        showDisconnectedGmailState();
        alert(data.message);
    } catch (error) {
        console.error(
            "Error disconnecting Gmail:",
            error
        );

        alert("Could not connect to the backend.");
    } finally {
        disconnectGmailBtn.disabled = false;
        disconnectGmailBtn.textContent = "Disconnect Gmail";
    }
}

// --- GMAIL SYNC AND SUGGESTIONS ---

function formatConfidence(confidence) {
    return `${Math.round(confidence * 100)}%`;
}


function createSuggestionCard(suggestion) {
    const card = document.createElement("article");
    card.className = "gmail-suggestion-card";
    card.dataset.suggestionId = suggestion.id;

    const header = document.createElement("div");
    header.className = "suggestion-card-header";

    const headingArea = document.createElement("div");

    const subject = document.createElement("h4");
    subject.textContent =
        suggestion.subject || "No email subject";

    const sender = document.createElement("p");
    sender.className = "suggestion-sender";
    sender.textContent =
        suggestion.sender || "Unknown sender";

    headingArea.appendChild(subject);
    headingArea.appendChild(sender);

    const statusBadge = document.createElement("span");
    const statusClass = (
        suggestion.suggested_status || "Unknown"
    ).toLowerCase();

    statusBadge.className =
        `badge badge-${statusClass}`;

    statusBadge.textContent =
        suggestion.suggested_status;

    header.appendChild(headingArea);
    header.appendChild(statusBadge);

    const details = document.createElement("div");
    details.className = "suggestion-details";

    const confidence = document.createElement("p");
    confidence.innerHTML = `
        <strong>Confidence:</strong>
        ${formatConfidence(suggestion.confidence)}
    `;

    const source = document.createElement("p");
    source.innerHTML = `
        <strong>Analysis:</strong>
        ${suggestion.analysis_source === "ai"
            ? "AI analysis"
            : "Initial filter"}
    `;

    const match = document.createElement("p");
    match.className = "suggestion-match-summary";

    if (suggestion.application_id !== null) {
        const matchedApplication = allApplications.find(
            (application) => application.id === suggestion.application_id
        );

        const matchName = matchedApplication
            ? `${matchedApplication.company_name} — ${matchedApplication.job_title}`
            : `Application ${suggestion.application_id}`;

        match.textContent = suggestion.match_score !== null
            ? `Suggested match: ${matchName} (${suggestion.match_score}/100)`
            : `Selected application: ${matchName}`;
    } else {
        match.textContent =
            "No reliable automatic match. Choose the application below.";
    }

    const applicationField = document.createElement("div");
    applicationField.className = "suggestion-application-field";

    const applicationLabel = document.createElement("label");
    const applicationSelectId =
        `suggestionApplication${suggestion.id}`;
    applicationLabel.htmlFor = applicationSelectId;
    applicationLabel.textContent = "Apply this update to";

    const applicationSelect = document.createElement("select");
    applicationSelect.id = applicationSelectId;
    applicationSelect.className = "suggestion-application-select";
    applicationSelect.dataset.suggestionId = suggestion.id;

    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = allApplications.length
        ? "Choose an application"
        : "Add an application first";
    applicationSelect.appendChild(emptyOption);

    [...allApplications]
        .sort((first, second) => {
            const firstName =
                `${first.company_name} ${first.job_title}`;
            const secondName =
                `${second.company_name} ${second.job_title}`;
            return firstName.localeCompare(secondName);
        })
        .forEach((application) => {
            const option = document.createElement("option");
            option.value = String(application.id);
            option.textContent =
                `${application.company_name} — ${application.job_title}`;
            applicationSelect.appendChild(option);
        });

    if (suggestion.application_id !== null) {
        applicationSelect.value = String(suggestion.application_id);
    }

    if (suggestion.suggested_status === "Unknown") {
        applicationSelect.disabled = true;
    }

    applicationField.appendChild(applicationLabel);
    applicationField.appendChild(applicationSelect);

    const reason = document.createElement("p");
    reason.className = "suggestion-reason";
    reason.textContent = suggestion.reason;

    details.appendChild(confidence);
    details.appendChild(source);
    details.appendChild(match);
    details.appendChild(applicationField);
    details.appendChild(reason);

    const actions = document.createElement("div");
    actions.className = "suggestion-actions";

    const confirmButton = document.createElement("button");
    confirmButton.type = "button";
    confirmButton.className = "confirm-suggestion-btn";
    confirmButton.textContent = "Confirm Update";
    confirmButton.dataset.suggestionId = suggestion.id;

    if (
        !applicationSelect.value ||
        suggestion.suggested_status === "Unknown"
    ) {
        confirmButton.disabled = true;
        confirmButton.title =
            suggestion.suggested_status === "Unknown"
                ? "Unknown cannot be applied as a status."
                : "Choose an application before confirming.";
    }

    const ignoreButton = document.createElement("button");
    ignoreButton.type = "button";
    ignoreButton.className = "ignore-suggestion-btn";
    ignoreButton.textContent = "Ignore";
    ignoreButton.dataset.suggestionId = suggestion.id;

    actions.appendChild(confirmButton);
    actions.appendChild(ignoreButton);

    card.appendChild(header);
    card.appendChild(details);
    card.appendChild(actions);

    return card;
}


function renderGmailSuggestions(suggestions) {
    currentGmailSuggestions = suggestions;
    gmailSuggestionsList.innerHTML = "";
    gmailSuggestionCount.textContent = suggestions.length;
    tabSuggestionCount.textContent = suggestions.length;
    tabSuggestionCount.classList.toggle(
        "hidden",
        suggestions.length === 0
    );

    if (suggestions.length === 0) {
        emptyGmailSuggestions.classList.remove("hidden");
        return;
    }

    emptyGmailSuggestions.classList.add("hidden");

    suggestions.forEach((suggestion) => {
        gmailSuggestionsList.appendChild(
            createSuggestionCard(suggestion)
        );
    });
}


async function fetchGmailSuggestions(
    { silent = false } = {}
) {
    if (
        !accessToken ||
        !gmailConnectionActive ||
        gmailSuggestionFetchInProgress
    ) {
        return;
    }

    gmailSuggestionFetchInProgress = true;

    try {
        const response = await fetch(
            `${GMAIL_URL}/suggestions`,
            {
                method: "GET",
                headers: getAuthHeaders()
            }
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.detail ||
                "Could not load Gmail suggestions."
            );
        }

        if (gmailConnectionActive) {
            renderGmailSuggestions(data);
        }
    } catch (error) {
        console.error(
            "Error loading Gmail suggestions:",
            error
        );

        if (!silent) {
            gmailSyncMessage.textContent =
                "Could not load pending suggestions.";

            gmailSyncMessage.classList.remove("hidden");
        }
    } finally {
        gmailSuggestionFetchInProgress = false;
    }
}


async function syncGmail() {
    if (!accessToken) {
        switchToLogin();
        return;
    }

    syncGmailBtn.disabled = true;
    syncGmailBtn.textContent = "Checking Gmail...";

    gmailSyncMessage.textContent =
        "Checking recent Gmail messages...";

    gmailSyncMessage.classList.remove("hidden");

    try {
        const response = await fetch(
            `${GMAIL_URL}/sync?max_results=10`,
            {
                method: "POST",
                headers: getAuthHeaders()
            }
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            gmailSyncMessage.textContent =
                data.detail ||
                "Gmail synchronization failed.";

            return;
        }

        gmailSyncMessage.textContent =
            `Checked ${data.messages_checked} messages. ` +
            `Created ${data.suggestions_created} new suggestions. ` +
            `Skipped ${data.duplicates_skipped} duplicates and ` +
            `${data.irrelevant_messages} unrelated messages.`;

        await fetchGmailSuggestions();
    } catch (error) {
        console.error(
            "Error synchronizing Gmail:",
            error
        );

        gmailSyncMessage.textContent =
            "Could not connect to the backend.";
    } finally {
        syncGmailBtn.disabled = false;
        syncGmailBtn.textContent = "Check Gmail Now";
    }
}


async function reviewGmailSuggestion(
    suggestionId,
    action,
    applicationId = null
) {
    if (!accessToken) {
        switchToLogin();
        return;
    }

    const actionText =
        action === "confirm" ? "confirm" : "ignore";

    const shouldContinue = window.confirm(
        `Are you sure you want to ${actionText} this suggestion?`
    );

    if (!shouldContinue) {
        return;
    }

    try {
        const requestOptions = {
            method: "POST",
            headers: getAuthHeaders(action === "confirm")
        };

        if (action === "confirm") {
            requestOptions.body = JSON.stringify({
                application_id: applicationId
            });
        }

        const response = await fetch(
            `${GMAIL_URL}/suggestions/${suggestionId}/${action}`,
            requestOptions
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            alert(
                data.detail ||
                "Could not review the Gmail suggestion."
            );

            return;
        }

        gmailSyncMessage.textContent = data.message;
        gmailSyncMessage.classList.remove("hidden");

        await fetchGmailSuggestions();

        if (action === "confirm") {
            await fetchApplications();
        }
    } catch (error) {
        console.error(
            "Error reviewing Gmail suggestion:",
            error
        );

        alert("Could not connect to the backend.");
    }
}

// --- TABLE CREATION ---

function createTableCell(text) {
    const cell = document.createElement("td");
    cell.textContent = text;

    return cell;
}


async function stopGmailMonitoring() {
    if (!accessToken) {
        switchToLogin();
        return;
    }

    startGmailMonitoringBtn.disabled = true;
    startGmailMonitoringBtn.textContent = "Disabling Monitoring...";

    gmailSyncMessage.textContent =
        "Disabling automatic Gmail monitoring...";
    gmailSyncMessage.classList.remove("hidden");

    try {
        const response = await fetch(
            `${GMAIL_URL}/monitoring/stop`,
            {
                method: "POST",
                headers: getAuthHeaders()
            }
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            gmailSyncMessage.textContent =
                data.detail ||
                "Automatic Gmail monitoring could not be disabled.";
            return;
        }

        gmailSyncMessage.textContent = data.message;
        await fetchGmailConnectionStatus();
    } catch (error) {
        console.error(
            "Error disabling Gmail monitoring:",
            error
        );

        gmailSyncMessage.textContent =
            "Could not connect to the backend.";
    } finally {
        startGmailMonitoringBtn.disabled = false;

        if (
            startGmailMonitoringBtn.textContent ===
            "Disabling Monitoring..."
        ) {
            startGmailMonitoringBtn.textContent =
                "Disable Automatic Monitoring";
        }
    }
}


function normalizeJobLink(value) {
    if (!value) {
        return null;
    }

    const linkWithProtocol = /^https?:\/\//i.test(value)
        ? value
        : `https://${value}`;

    try {
        const parsedUrl = new URL(linkWithProtocol);

        if (!["http:", "https:"].includes(parsedUrl.protocol)) {
            return null;
        }

        return parsedUrl.href;
    } catch {
        return null;
    }
}


function createJobRow(job) {
    const row = document.createElement("tr");

    const companyCell = document.createElement("td");
    companyCell.className = "company-cell";
    const companyName = document.createElement("strong");

    companyName.textContent = job.company_name;
    companyCell.appendChild(companyName);

    const roleCell = createTableCell(job.job_title);
    const locationCell = createTableCell(
        job.location || "N/A"
    );

    const displayDate = job.date_applied
        ? job.date_applied.split("T")[0]
        : "N/A";

    const dateCell = createTableCell(displayDate);

    const statusCell = document.createElement("td");
    const statusBadge = document.createElement("span");

    const statusText = job.status || "Applied";
    const statusClass = statusText.toLowerCase();

    statusBadge.className =
        `badge badge-${statusClass}`;

    statusBadge.textContent = statusText;

    statusCell.appendChild(statusBadge);

    const linkCell = document.createElement("td");
    const safeJobLink = normalizeJobLink(job.job_link);

    if (safeJobLink) {
        const jobLink = document.createElement("a");
        jobLink.className = "job-link";
        jobLink.href = safeJobLink;
        jobLink.target = "_blank";
        jobLink.rel = "noopener noreferrer";
        jobLink.textContent = "Open ↗";
        jobLink.setAttribute(
            "aria-label",
            `Open link for ${job.company_name}`
        );
        linkCell.appendChild(jobLink);
    } else {
        const noLink = document.createElement("span");
        noLink.className = "no-link";
        noLink.textContent = "—";
        linkCell.appendChild(noLink);
    }

    const actionCell = document.createElement("td");
    actionCell.className = "job-action-cell";
    const deleteButton = document.createElement("button");

    deleteButton.type = "button";
    deleteButton.className = "delete-btn";
    deleteButton.textContent = "Delete";
    deleteButton.dataset.applicationId = job.id;

    actionCell.appendChild(deleteButton);

    companyCell.dataset.label = "Company";
    roleCell.dataset.label = "Role";
    locationCell.dataset.label = "Location";
    dateCell.dataset.label = "Date applied";
    statusCell.dataset.label = "Status";
    linkCell.dataset.label = "Link";
    actionCell.dataset.label = "Actions";

    row.appendChild(companyCell);
    row.appendChild(roleCell);
    row.appendChild(locationCell);
    row.appendChild(dateCell);
    row.appendChild(statusCell);
    row.appendChild(linkCell);
    row.appendChild(actionCell);

    return row;
}


// --- FETCH APPLICATIONS ---

async function fetchApplications() {
    if (!accessToken) {
        return;
    }

    try {
        const response = await fetch(API_URL, {
            method: "GET",
            headers: getAuthHeaders()
        });

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            alert(
                "Your login has expired. Please log in again."
            );

            switchToLogin();
            return;
        }

        if (!response.ok) {
            throw new Error(
                "Failed to fetch applications."
            );
        }

        allApplications = await response.json();
        updateApplicationCounters(allApplications);
        renderApplications();

        if (currentGmailSuggestions.length > 0) {
            renderGmailSuggestions(currentGmailSuggestions);
        }
    } catch (error) {
        console.error(
            "Error communicating with the backend:",
            error
        );

        alert("Could not load your applications.");
    }
}


// --- DELETE APPLICATION ---

async function deleteApplication(applicationId) {
    if (!accessToken) {
        alert(
            "You must be logged in to delete an application."
        );

        switchToLogin();
        return;
    }

    const shouldDelete = window.confirm(
        "Are you sure you want to delete this application?"
    );

    if (!shouldDelete) {
        return;
    }

    try {
        const response = await fetch(
            `${API_URL}/${applicationId}`,
            {
                method: "DELETE",
                headers: getAuthHeaders()
            }
        );

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            alert(
                "Your login has expired. Please log in again."
            );

            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            alert(
                data.detail ||
                "Could not delete the application."
            );

            return;
        }

        await fetchApplications();
    } catch (error) {
        console.error(
            "Network error while deleting application:",
            error
        );

        alert("Could not connect to the backend.");
    }
}


// --- PASSWORD VISIBILITY AND RECOVERY ---

passwordToggleButtons.forEach((toggleButton) => {
    toggleButton.addEventListener("click", () => {
        const passwordInput = document.getElementById(
            toggleButton.dataset.passwordTarget
        );

        const shouldShowPassword =
            passwordInput.type === "password";

        passwordInput.type = shouldShowPassword
            ? "text"
            : "password";

        toggleButton.setAttribute(
            "aria-pressed",
            String(shouldShowPassword)
        );

        toggleButton.setAttribute(
            "aria-label",
            shouldShowPassword
                ? "Hide password"
                : "Show password"
        );
    });
});


[authEmail, authPassword, authConfirmPassword].forEach((authInput) => {
    authInput.addEventListener("input", () => {
        authConfirmPassword.setCustomValidity("");
        hideAuthMessage();
    });
});


forgotPasswordBtn.addEventListener("click", () => {
    showAuthMessage(
        "Secure password recovery is the next feature being added. " +
        "It is not available yet."
    );
});


// --- LOGIN / SIGNUP TOGGLE ---

authToggle.addEventListener("click", (event) => {
    if (event.target.id !== "toggleAuthMode") {
        return;
    }

    isLoginMode = !isLoginMode;
    updateAuthScreen();
});


// --- LOGIN / SIGNUP SUBMISSION ---

authForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    hideAuthMessage();

    if (!authForm.reportValidity()) {
        return;
    }

    const email = authEmail.value.trim();
    const password = authPassword.value;

    if (!isLoginMode && password !== authConfirmPassword.value) {
        authConfirmPassword.setCustomValidity(
            "The passwords do not match."
        );
        authConfirmPassword.reportValidity();

        showAuthMessage(
            "The passwords do not match. Please try again.",
            { isError: true }
        );

        return;
    }

    const endpoint = isLoginMode
        ? `${AUTH_URL}/login`
        : `${AUTH_URL}/signup`;

    try {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                email,
                password
            })
        });

        const data = await response.json();

        if (!response.ok) {
            showAuthMessage(
                data.detail ||
                "Authentication failed.",
                { isError: true }
            );

            return;
        }

        if (isLoginMode) {
            // NEW: Store the JWT returned by FastAPI.
            accessToken = data.access_token;

            switchToDashboard();
        } else {
            isLoginMode = true;
            updateAuthScreen();

            authPassword.value = "";
            authConfirmPassword.value = "";

            showAuthMessage(
                "Your account was created. You can log in now."
            );
        }
    } catch (error) {
        console.error(
            "Authentication system error:",
            error
        );

        showAuthMessage(
            "Could not connect to the authentication server.",
            { isError: true }
        );
    }
});


// --- LOGOUT ---

logoutBtn.addEventListener("click", () => {
    switchToLogin();
});


// --- CREATE APPLICATION ---

jobForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!accessToken) {
        alert(
            "You must log in before adding an application."
        );

        switchToLogin();
        return;
    }

    const companyName = document
        .getElementById("companyName")
        .value
        .trim();

    const jobTitle = document
        .getElementById("jobTitle")
        .value
        .trim();

    const location = document
        .getElementById("location")
        .value
        .trim();

    const jobLinkInput = document
        .getElementById("jobLink")
        .value
        .trim();

    const jobLink = normalizeJobLink(jobLinkInput);

    if (jobLinkInput && !jobLink) {
        alert("Please enter a valid company or job URL.");
        return;
    }

    const payload = {
        company_name: companyName,
        job_title: jobTitle,
        location: location || null,
        job_link: jobLink
    };

    try {
        const response = await fetch(API_URL, {
            method: "POST",
            headers: getAuthHeaders(true),
            body: JSON.stringify(payload)
        });

        if (
            response.status === 401 ||
            response.status === 403
        ) {
            alert(
                "Your login has expired. Please log in again."
            );

            switchToLogin();
            return;
        }

        const data = await response.json();

        if (!response.ok) {
            alert(
                data.detail ||
                "Error saving job application."
            );

            return;
        }

        jobForm.reset();
        await fetchApplications();
    } catch (error) {
        console.error(
            "Network error while saving application:",
            error
        );

        alert("Could not connect to the backend.");
    }
});


// --- APPLICATION FILTER AND PAGINATION HANDLERS ---

jobSearch.addEventListener("input", () => {
    currentApplicationsPage = 1;
    renderApplications();
});

statusFilter.addEventListener("change", () => {
    currentApplicationsPage = 1;
    renderApplications();
});

jobSort.addEventListener("change", () => {
    currentApplicationsPage = 1;
    renderApplications();
});

pageSize.addEventListener("change", () => {
    currentApplicationsPage = 1;
    renderApplications();
});

clearFiltersBtn.addEventListener("click", () => {
    jobSearch.value = "";
    statusFilter.value = "";
    jobSort.value = "newest";
    currentApplicationsPage = 1;
    renderApplications();
});

previousPageBtn.addEventListener("click", () => {
    if (currentApplicationsPage > 1) {
        currentApplicationsPage -= 1;
        renderApplications();
    }
});

nextPageBtn.addEventListener("click", () => {
    currentApplicationsPage += 1;
    renderApplications();
});

statCards.forEach((card) => {
    card.addEventListener("click", () => {
        switchDashboardView("jobs");
        statusFilter.value = card.dataset.statusFilter;
        currentApplicationsPage = 1;
        renderApplications();
    });
});


dashboardTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => {
        switchDashboardView(tab.dataset.dashboardView);
    });

    tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) {
            return;
        }

        event.preventDefault();
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const nextIndex =
            (index + direction + dashboardTabs.length)
            % dashboardTabs.length;

        switchDashboardView(
            dashboardTabs[nextIndex].dataset.dashboardView,
            { focusTab: true }
        );
    });
});


dashboardSectionSelect.addEventListener("change", () => {
    switchDashboardView(dashboardSectionSelect.value);
});


// --- DELETE BUTTON CLICK HANDLER ---

jobsTableBody.addEventListener("click", (event) => {
    const deleteButton = event.target.closest(
        ".delete-btn"
    );

    if (!deleteButton) {
        return;
    }

    const applicationId = Number(
        deleteButton.dataset.applicationId
    );

    if (!Number.isInteger(applicationId)) {
        alert("Invalid application ID.");
        return;
    }

    deleteApplication(applicationId);
});

// --- GMAIL BUTTON HANDLERS ---

connectGmailBtn.addEventListener("click", () => {
    connectGmail();
});


disconnectGmailBtn.addEventListener("click", () => {
    disconnectGmail();
});

startGmailMonitoringBtn.addEventListener("click", () => {
    if (gmailMonitoringBadge.classList.contains("active")) {
        stopGmailMonitoring();
        return;
    }

    startGmailMonitoring();
});

syncGmailBtn.addEventListener("click", () => {
    syncGmail();
});

gmailSuggestionsList.addEventListener(
    "click",
    (event) => {
        const confirmButton = event.target.closest(
            ".confirm-suggestion-btn"
        );

        if (confirmButton) {
            const suggestionId = Number(
                confirmButton.dataset.suggestionId
            );

            const suggestionCard = confirmButton.closest(
                ".gmail-suggestion-card"
            );
            const applicationSelect = suggestionCard.querySelector(
                ".suggestion-application-select"
            );
            const applicationId = Number(applicationSelect.value);

            if (
                Number.isInteger(suggestionId) &&
                Number.isInteger(applicationId) &&
                applicationId > 0
            ) {
                reviewGmailSuggestion(
                    suggestionId,
                    "confirm",
                    applicationId
                );
            }

            return;
        }

        const ignoreButton = event.target.closest(
            ".ignore-suggestion-btn"
        );

        if (ignoreButton) {
            const suggestionId = Number(
                ignoreButton.dataset.suggestionId
            );

            if (Number.isInteger(suggestionId)) {
                reviewGmailSuggestion(
                    suggestionId,
                    "ignore"
                );
            }

            return;
        }

        const applicationSelect = event.target.closest(
            ".suggestion-application-select"
        );

        if (applicationSelect) {
            const suggestionCard = applicationSelect.closest(
                ".gmail-suggestion-card"
            );
            const confirmButton = suggestionCard.querySelector(
                ".confirm-suggestion-btn"
            );
            const statusBadge = suggestionCard.querySelector(".badge");

            confirmButton.disabled =
                !applicationSelect.value ||
                statusBadge.textContent === "Unknown";

            confirmButton.title = confirmButton.disabled
                ? "Choose an application before confirming."
                : "Confirm this status update.";
        }
    }
);

/*
After Google OAuth finishes in another tab, returning to this
browser window refreshes the Gmail connection state.
*/
window.addEventListener("focus", () => {
    if (accessToken) {
        fetchGmailConnectionStatus();
    }
});
