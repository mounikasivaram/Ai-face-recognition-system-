let cameraStream = null;
let activeVideo = null;
let knownParticipants = [];
let currentPrediction = null;
const currentRole = document.body.dataset.currentRole;
const currentUsername = document.body.dataset.currentUsername;

document.querySelectorAll("[data-admin-only]").forEach((element) => {
  if (currentRole !== "admin") element.hidden = true;
});

const pages = document.querySelectorAll(".page");
const navButtons = document.querySelectorAll(".nav-button");

function showMessage(elementId, text, type = "") {
  const element = document.getElementById(elementId);
  element.textContent = text;
  element.className = `message ${type}`;
}

function stopCamera(messageId = null) {
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }

  document.querySelectorAll("video").forEach((video) => {
    video.srcObject = null;
    video.classList.remove("active");
  });

  document.querySelectorAll(".camera-placeholder").forEach((placeholder) => {
    placeholder.hidden = false;
  });

  document.getElementById("captureEnroll").disabled = true;
  document.getElementById("captureRecognize").disabled = true;
  activeVideo = null;
  if (messageId) showMessage(messageId, "Camera stopped.", "success");
}

function openPage(pageName) {
  stopCamera();

  pages.forEach((page) => {
    page.classList.toggle("active", page.id === pageName);
  });

  navButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.page === pageName);
  });

  if (pageName === "overview" || pageName === "train") {
    loadStatus();
  }
  if (pageName === "overview") {
    loadHomeAttendance();
  }
  if (pageName === "history") {
    loadHistory();
  }
  if (pageName === "attendance") {
    loadAttendance();
    loadMonthlyReport();
  }
  if (pageName === "access") {
    loadAccess();
  }
  if (pageName === "visitor-passes") {
    loadVisitorPasses();
  }
  if (pageName === "users" && currentRole === "admin") {
    loadUsers();
  }
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => openPage(button.dataset.page));
});

document.querySelectorAll("[data-open-page]").forEach((button) => {
  button.addEventListener("click", () => openPage(button.dataset.openPage));
});

async function startCamera(videoId, placeholderId, captureButtonId, messageId) {
  stopCamera();

  if (!navigator.mediaDevices?.getUserMedia) {
    const message = window.isSecureContext
      ? "This browser cannot access the camera. Check camera permissions or try another browser."
      : "Phone camera access needs the app's HTTPS website link. A local Wi-Fi address starting with http:// cannot open the camera. Use photo upload where available.";
    showMessage(messageId, message, "error");
    return;
  }

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" },
      audio: false,
    });

    activeVideo = document.getElementById(videoId);
    activeVideo.srcObject = cameraStream;
    activeVideo.classList.add("active");
    document.getElementById(placeholderId).hidden = true;

    if (activeVideo.readyState < 1) {
      await new Promise((resolve) => {
        activeVideo.onloadedmetadata = resolve;
      });
    }

    await activeVideo.play();
    document.getElementById(captureButtonId).disabled = false;
    showMessage(messageId, "Camera is ready.", "success");
  } catch (error) {
    showMessage(
      messageId,
      `Could not open camera: ${error.message}. Check browser camera permission.`,
      "error"
    );
  }
}

function capturePhoto(videoId) {
  const video = document.getElementById(videoId);

  if (!video.videoWidth || !video.videoHeight) {
    return Promise.reject(new Error("Camera is not ready yet."));
  }

  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error("Could not capture photo."));
      }
    }, "image/jpeg");
  });
}

async function postPhoto(url, photo, fields = {}, filename = "capture.jpg") {
  const form = new FormData();
  form.append("image", photo, filename);

  Object.entries(fields).forEach(([key, value]) => {
    form.append(key, value);
  });

  const response = await fetch(url, { method: "POST", body: form });
  const result = await response.json();

  if (!response.ok) {
    throw new Error(result.error || "Request failed.");
  }
  return result;
}

/* Enrollment */
document.getElementById("startEnrollCamera").addEventListener("click", () => {
  startCamera(
    "enrollVideo",
    "enrollPlaceholder",
    "captureEnroll",
    "enrollMessage"
  );
});
document.getElementById("stopEnrollCamera").addEventListener("click", () => {
  stopCamera("enrollMessage");
});

document.getElementById("captureEnroll").addEventListener("click", async () => {
  const name = document.getElementById("participantName").value.trim();
  const button = document.getElementById("captureEnroll");
  if (!name) {
    showMessage("enrollMessage", "Enter the enrolled person's name.", "error");
    return;
  }

  button.disabled = true;
  try {
    showMessage("enrollMessage", "Saving face image...");
    const photo = await capturePhoto("enrollVideo");
    const category = document.getElementById("participantCategory").value;
    const result = await postPhoto("/api/enroll", photo, { name, category });
    showMessage("enrollMessage", result.message, "success");
    await loadStatus();
  } catch (error) {
    showMessage("enrollMessage", error.message, "error");
  } finally {
    button.disabled = !cameraStream;
  }
});

/* Model training */
document.getElementById("trainButton").addEventListener("click", async () => {
  const button = document.getElementById("trainButton");
  button.disabled = true;
  showMessage("trainMessage", "Training model...");

  try {
    const response = await fetch("/api/train", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Training failed.");

    showMessage("trainMessage", result.message, "success");
    await loadStatus();
  } catch (error) {
    showMessage("trainMessage", error.message, "error");
  } finally {
    button.disabled = false;
  }
});

/* Recognition */
document.getElementById("startRecognizeCamera").addEventListener("click", () => {
  startCamera(
    "recognizeVideo",
    "recognizePlaceholder",
    "captureRecognize",
    "recognizeMessage"
  );
});
document.getElementById("stopRecognizeCamera").addEventListener("click", () => {
  stopCamera("recognizeMessage");
});

async function recognizePhoto(photo, filename = "capture.jpg") {
  showMessage("recognizeMessage", "Checking face...");
  document.getElementById("recognizeResults").replaceChildren();
  document.getElementById("evaluationPanel").hidden = true;

  const threshold = document.getElementById("threshold").value;
  const result = await postPhoto(
    "/api/recognize",
    photo,
    { threshold },
    filename
  );

  displayRecognition(result);
}

function displayRecognition(result) {
  const resultsArea = document.getElementById("recognizeResults");
  resultsArea.replaceChildren();

  if (result.image) {
    const image = document.createElement("img");
    image.src = `data:image/jpeg;base64,${result.image}`;
    image.alt = "Recognition result";
    resultsArea.appendChild(image);
  }

  const predictions = result.results || [];
  predictions.forEach((person) => {
    const badge = document.createElement("span");
    badge.className =
      `result-badge${person.name === "Unknown" ? " unknown" : ""}`;
    badge.textContent = `${person.name} · distance ${person.distance}`;
    resultsArea.appendChild(badge);

    const accessBadge = document.createElement("span");
    accessBadge.className = `access-result ${person.access_decision === "granted" ? "granted" : "denied"}`;
    accessBadge.textContent = person.access_decision === "granted"
      ? "ACCESS GRANTED · authorized"
      : `ACCESS DENIED · ${person.access_reason || "not authorized"}`;
    resultsArea.appendChild(accessBadge);

    if (person.name !== "Unknown") {
      const attendanceActions = document.createElement("div");
      attendanceActions.className = "attendance-event";
      [["check_in", "Check in"], ["check_out", "Check out"]].forEach(([event, label]) => {
        const action = document.createElement("button");
        action.type = "button";
        action.textContent = label;
        action.addEventListener("click", () => recordAttendance(person.participant_id || person.name, event));
        attendanceActions.appendChild(action);
      });
      resultsArea.appendChild(attendanceActions);
    }
  });

  if (predictions.length > 0) {
    currentPrediction = predictions[0];
    document.getElementById("evaluationPanel").hidden = false;
    fillExpectedNames();
    showMessage("recognizeMessage", "Recognition complete.", "success");
  } else {
    currentPrediction = null;
    showMessage(
      "recognizeMessage",
      result.message || "No face detected.",
      ""
    );
  }
}

document.getElementById("captureRecognize").addEventListener("click", async () => {
  const button = document.getElementById("captureRecognize");
  button.disabled = true;

  try {
    const photo = await capturePhoto("recognizeVideo");
    await recognizePhoto(photo);
  } catch (error) {
    showMessage("recognizeMessage", error.message, "error");
  } finally {
    button.disabled = !cameraStream;
  }
});

document.getElementById("recognizeUploadButton").addEventListener("click", async () => {
  const input = document.getElementById("recognizeUpload");
  const file = input.files[0];

  if (!file) {
    showMessage("recognizeMessage", "Choose an image file first.", "error");
    return;
  }

  try {
    await recognizePhoto(file, file.name);
  } catch (error) {
    showMessage("recognizeMessage", error.message, "error");
  }
});

document.getElementById("threshold").addEventListener("input", (event) => {
  document.getElementById("thresholdValue").textContent = event.target.value;
});

/* Save a manual evaluation */
function fillExpectedNames() {
  const select = document.getElementById("expectedName");
  const previousValue = select.value;
  select.replaceChildren();

  [...knownParticipants.map((person) => person.id), "Unknown"].forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  });

  if ([...select.options].some((option) => option.value === previousValue)) {
    select.value = previousValue;
  }
}

document.getElementById("saveEvaluation").addEventListener("click", async () => {
  if (!currentPrediction) {
    showMessage("evaluationMessage", "Recognize a face first.", "error");
    return;
  }

  const form = new FormData();
  form.append("predicted", currentPrediction.name);
  form.append("distance", currentPrediction.distance);
  form.append("expected", document.getElementById("expectedName").value);

  try {
    const response = await fetch("/api/evaluate", { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save evaluation.");

    const note = result.correct ? "Correct match recorded." : "Incorrect match recorded.";
    showMessage("evaluationMessage", `${result.message} ${note}`, "success");
    await loadStatus();
    await loadHistory();
  } catch (error) {
    showMessage("evaluationMessage", error.message, "error");
  }
});

/* Participant previews and deletion */
document.getElementById("participantList").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;

  const name = button.dataset.name;
  const person = knownParticipants.find((item) => item.id === name);
  const displayName = person?.name || "this participant";

  if (button.dataset.action === "preview") {
    await showParticipantImages(name);
  }

  if (button.dataset.action === "delete") {
    const confirmed = window.confirm(
      `Delete ${displayName} and all saved face images? The model will need retraining.`
    );
    if (!confirmed) return;

    const form = new FormData();
    form.append("name", name);

    try {
      const response = await fetch("/api/delete", { method: "POST", body: form });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Delete failed.");

      window.alert(result.message);
      document.getElementById("participantGallerySection").hidden = true;
      await loadStatus();
    } catch (error) {
      window.alert(error.message);
    }
  }
});

async function showParticipantImages(name) {
  const section = document.getElementById("participantGallerySection");
  const gallery = document.getElementById("participantGallery");
  const title = document.getElementById("galleryTitle");

  const person = knownParticipants.find((item) => item.id === name);
  const displayName = person?.name || "Participant";
  title.textContent = `${displayName} · saved images`;
  gallery.replaceChildren();
  section.hidden = false;

  try {
    const response = await fetch(
      `/api/participants/${encodeURIComponent(name)}/images`
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load images.");

    if (result.images.length === 0) {
      gallery.textContent = "No saved images.";
      return;
    }

    result.images.forEach((filename) => {
      const image = document.createElement("img");
      image.src =
        `/api/participants/${encodeURIComponent(name)}/images/` +
        encodeURIComponent(filename);
      image.alt = `${displayName} face sample`;
      image.loading = "lazy";
      gallery.appendChild(image);
    });
  } catch (error) {
    gallery.textContent = error.message;
  }
}

document.getElementById("closeGallery").addEventListener("click", () => {
  document.getElementById("participantGallerySection").hidden = true;
});

/* Dashboard status and dataset quality */
function addParticipantRow(person) {
  const row = document.createElement("div");
  row.className = "participant-row";

  const label = document.createElement("div");
  label.className = "participant-name";

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = person.category.slice(0, 1).toUpperCase();

  const name = document.createElement("span");
  name.textContent = person.name;

  const category = document.createElement("span");
  category.className = "person-category";
  category.textContent = person.category || "Participant";

  label.append(avatar, name, category);

  const count = document.createElement("span");
  count.className = "image-count";
  count.textContent = `${person.images} image(s)`;

  const quality = document.createElement("span");
  quality.className = person.quality_ok ? "quality-badge good" : "quality-badge needs-more";
  quality.textContent = person.quality_ok
    ? (person.images >= person.quality_target ? "Recommended" : "Ready; 1 more recommended")
    : `Need ${Math.max(0, person.minimum_images - person.images)} more to train`;

  const actions = document.createElement("div");
  actions.className = "participant-actions";

  const preview = document.createElement("button");
  preview.className = "small-button";
  preview.textContent = "Preview";
  preview.dataset.action = "preview";
  preview.dataset.name = person.id;

  const deleteButton = document.createElement("button");
  deleteButton.className = "small-button delete-button";
  deleteButton.textContent = "Delete";
  deleteButton.dataset.action = "delete";
  deleteButton.dataset.name = person.id;

  if (currentRole === "admin") actions.append(preview, deleteButton);
  row.append(label, count, quality, actions);
  return row;
}

function updateQualityList(participants) {
  const list = document.getElementById("qualityList");
  list.replaceChildren();

  if (participants.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Enroll participants to check dataset quality.";
    list.appendChild(empty);
    return;
  }

  participants.forEach((person) => {
    const row = document.createElement("div");
    row.className = "quality-row";

    const label = document.createElement("strong");
    label.textContent = person.name;

    const count = document.createElement("span");
    count.textContent = `${person.images} images (${person.quality_target} recommended)`;

    const badge = document.createElement("span");
    badge.className = person.quality_ok ? "quality-badge good" : "quality-badge needs-more";
    badge.textContent = person.quality_ok
      ? (person.images >= person.quality_target ? "Recommended count reached" : "Minimum met")
      : `Need ${Math.max(0, person.minimum_images - person.images)} more to train`;

    row.append(label, count, badge);
    list.appendChild(row);
  });
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const status = await response.json();
    if (!response.ok) throw new Error("Could not load status.");

    knownParticipants = status.participants || [];
    const imageTotal = knownParticipants.reduce(
      (sum, person) => sum + person.images,
      0
    );
    const needsMore = knownParticipants.filter((person) => !person.quality_ok).length;

    document.getElementById("participantsCount").textContent = knownParticipants.length;
    document.getElementById("imageCount").textContent = imageTotal;
    document.getElementById("qualitySummary").textContent = needsMore
      ? `${needsMore} participant(s) need at least 3 images to train`
      : "Minimum met; 4 images per person recommended";

    document.getElementById("modelStatus").textContent =
      status.model_ready ? "Ready" : "Not trained";
    document.getElementById("modelHelp").textContent = status.model_ready
      ? "You can run recognition"
      : "Train after enrolling at least two people";

    const evaluation = status.evaluation || {};
    document.getElementById("evaluationAccuracy").textContent =
      evaluation.accuracy === null || evaluation.accuracy === undefined
        ? "Not tested"
        : `${evaluation.accuracy}%`;
    document.getElementById("evaluationCount").textContent =
      `${evaluation.correct || 0} correct / ${evaluation.total || 0} evaluated`;

    const participantList = document.getElementById("participantList");
    participantList.replaceChildren();

    if (knownParticipants.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No participants enrolled yet. Use “Enroll person” to begin.";
      participantList.appendChild(empty);
    } else {
      knownParticipants.forEach((person) => {
        participantList.appendChild(addParticipantRow(person));
      });
    }

    updateQualityList(knownParticipants);
    fillExpectedNames();
  } catch (error) {
    console.error("Could not load project status:", error);
  }
}

/* Recognition and evaluation history */
function addTableCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value || "—";
  row.appendChild(cell);
}

async function loadHistory() {
  const tableBody = document.getElementById("historyTableBody");

  try {
    const response = await fetch("/api/history");
    const result = await response.json();
    if (!response.ok) throw new Error("Could not load history.");

    tableBody.replaceChildren();

    if (!result.rows || result.rows.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = currentRole === "admin" ? 7 : 6;
      cell.textContent = "No history yet.";
      row.appendChild(cell);
      tableBody.appendChild(row);
      return;
    }

    result.rows.forEach((item) => {
      const row = document.createElement("tr");
      addTableCell(row, item.time);
      addTableCell(row, item.event === "evaluation" ? "Evaluation" : "Recognition");
      addTableCell(row, item.predicted);
      addTableCell(row, item.expected);
      addTableCell(row, item.distance);
      addTableCell(row, item.correct);
      if (currentRole === "admin") {
        const actionCell = document.createElement("td");
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "small-button delete-button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteHistoryRecord(item.row_id));
        actionCell.appendChild(deleteButton);
        row.appendChild(actionCell);
      }
      tableBody.appendChild(row);
    });
  } catch (error) {
    tableBody.textContent = error.message;
  }
}

async function deleteHistoryRecord(rowId) {
  if (!window.confirm("Delete this history record? This cannot be undone.")) return;
  try {
    const response = await fetch(`/api/history/${rowId}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not delete history record.");
    await loadHistory();
  } catch (error) {
    window.alert(error.message);
  }
}

async function recordAttendance(participant, event) {
  const form = new FormData();
  form.append("participant", participant);
  form.append("event", event);
  try {
    const response = await fetch("/api/attendance", { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not record attendance.");
    showMessage("recognizeMessage", result.message, "success");
    await loadAttendance();
    await loadHomeAttendance();
  } catch (error) {
    showMessage("recognizeMessage", error.message, "error");
  }
}

async function loadHomeAttendance() {
  const tableBody = document.getElementById("homeAttendanceBody");
  const dateLabel = document.getElementById("homeAttendanceDate");
  if (!tableBody || !dateLabel) return;
  try {
    const response = await fetch("/api/attendance/summary");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load today's attendance.");
    dateLabel.textContent = `Attendance date: ${result.date}`;
    tableBody.replaceChildren();
    if (!result.people.length) {
      const row = document.createElement("tr");
      row.innerHTML = '<td colspan="5">No participants enrolled yet.</td>';
      tableBody.appendChild(row);
      return;
    }
    result.people.forEach((person) => {
      const row = document.createElement("tr");
      addTableCell(row, person.name);
      addTableCell(row, person.category);
      const status = document.createElement("td");
      status.className = person.status === "Present" ? "attendance-present" : "attendance-absent";
      status.textContent = person.status;
      row.appendChild(status);
      addTableCell(row, person.last_event);
      addTableCell(row, person.event_time === "—" ? "—" : person.event_time.replace("T", " "));
      tableBody.appendChild(row);
    });
  } catch (error) {
    dateLabel.textContent = "";
    tableBody.textContent = error.message;
  }
}

async function loadAccess() {
  const participantList = document.getElementById("accessParticipantList");
  const tableBody = document.getElementById("accessLogTableBody");
  try {
    const response = await fetch("/api/access");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load access control data.");

    participantList.replaceChildren();
    if (!result.participants.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Enroll participants before setting access permissions.";
      participantList.appendChild(empty);
    }

    result.participants.forEach((person) => {
      const row = document.createElement("div");
      row.className = "quality-row access-person-row";
      const label = document.createElement("strong");
      label.textContent = person.name;
      const category = document.createElement("span");
      category.className = "access-category";
      category.textContent = person.category || "Participant";
      const state = document.createElement("span");
      state.className = `access-state ${person.authorized ? "granted" : "denied"}`;
      state.textContent = person.pass_required
        ? "Approved pass required"
        : (person.authorized ? "Authorized" : "Not authorized");
      row.append(label, category, state);

      if (currentRole === "admin" && !person.pass_required) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "small-button";
        button.textContent = person.authorized ? "Revoke access" : "Authorize";
        button.addEventListener("click", () => setAccessPermission(person));
        row.appendChild(button);
      }
      participantList.appendChild(row);
    });

    tableBody.replaceChildren();
    if (!result.logs.length) {
      const row = document.createElement("tr");
      row.innerHTML = "<td colspan=\"6\">No access attempts yet.</td>";
      tableBody.appendChild(row);
    }
    result.logs.forEach((item) => {
      const row = document.createElement("tr");
      addTableCell(row, item.event_time.replace("T", " "));
      addTableCell(row, item.participant);
      const decision = document.createElement("td");
      decision.className = `access-state ${item.decision}`;
      decision.textContent = item.decision === "granted" ? "Access granted" : "Access denied";
      row.appendChild(decision);
      addTableCell(row, item.reason);
      addTableCell(row, item.distance === null ? "—" : item.distance);
      addTableCell(row, item.operator);
      tableBody.appendChild(row);
    });
  } catch (error) {
    participantList.textContent = error.message;
    tableBody.textContent = error.message;
  }
}

async function loadVisitorPasses() {
  const table = document.getElementById("visitorPassRows");
  const visitorSelect = document.getElementById("visitorPassPerson");
  const hostSelect = document.getElementById("visitorPassHost");
  try {
    const response = await fetch("/api/visitor-passes", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load visitor passes.");
    for (const [select, values, key, label] of [
      [visitorSelect, result.visitors, "id", "name"],
      [hostSelect, result.hosts, "username", "label"],
    ]) {
      const selected = select.value;
      select.replaceChildren(new Option(select === visitorSelect ? "Choose a visitor" : "Choose a host account", ""));
      values.forEach((item) => select.add(new Option(item[label], item[key])));
      if (values.some((item) => item[key] === selected)) select.value = selected;
    }
    table.replaceChildren();
    if (!result.passes.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td"); cell.colSpan = 7; cell.textContent = "No visitor requests yet.";
      row.appendChild(cell); table.appendChild(row);
    }
    result.passes.forEach((pass) => {
      const row = document.createElement("tr");
      [pass.visitor_name, pass.host_username, pass.purpose,
        `${pass.starts_at.replace("T", " ")} – ${pass.ends_at.replace("T", " ")}`,
        pass.status, pass.pass_code || "—"].forEach((value) => addTableCell(row, value));
      const action = document.createElement("td");
      const isAssignedHost = currentUsername.toLocaleLowerCase() === pass.host_username.toLocaleLowerCase();
      if (pass.status === "pending" && (currentRole === "admin" || isAssignedHost)) {
        for (const decision of ["approved", "rejected"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = decision === "approved" ? "small-button" : "small-button delete-button";
          button.textContent = decision === "approved" ? "Approve" : "Reject";
          button.addEventListener("click", () => decideVisitorPass(pass.id, decision));
          action.appendChild(button);
        }
      } else action.textContent = "—";
      row.appendChild(action);
      table.appendChild(row);
    });
  } catch (error) {
    table.textContent = error.message;
    showMessage("visitorPassMessage", error.message, "error");
  }
}

async function decideVisitorPass(passId, decision) {
  const form = new FormData();
  form.append("decision", decision);
  try {
    const response = await fetch(`/api/visitor-passes/${passId}/decision`, { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not update visitor request.");
    showMessage("visitorPassMessage", result.pass_code ? `${result.message} Pass code: ${result.pass_code}` : result.message, "success");
    await loadVisitorPasses();
  } catch (error) {
    showMessage("visitorPassMessage", error.message, "error");
  }
}

async function createVisitorPass() {
  const form = new FormData();
  form.append("participant_id", document.getElementById("visitorPassPerson").value);
  form.append("host_username", document.getElementById("visitorPassHost").value);
  form.append("purpose", document.getElementById("visitorPassPurpose").value.trim());
  form.append("starts_at", document.getElementById("visitorPassStarts").value);
  form.append("ends_at", document.getElementById("visitorPassEnds").value);
  try {
    const response = await fetch("/api/visitor-passes", { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not create visitor pass.");
    showMessage("visitorPassMessage", result.message, "success");
    document.getElementById("visitorPassPurpose").value = "";
    await loadVisitorPasses();
  } catch (error) {
    showMessage("visitorPassMessage", error.message, "error");
  }
}

async function setAccessPermission(person) {
  const nextAuthorization = !person.authorized;
  const question = nextAuthorization
    ? `Allow ${person.name} in the access-control demo?`
    : `Revoke demo access for ${person.name}?`;
  if (!window.confirm(question)) return;

  const form = new FormData();
  form.append("authorized", String(nextAuthorization));
  try {
    const response = await fetch(
      `/api/participants/${encodeURIComponent(person.id)}/access`,
      { method: "POST", body: form }
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not update access permission.");
    await loadAccess();
    await loadStatus();
  } catch (error) {
    window.alert(error.message);
  }
}

async function loadAttendance() {
  const tableBody = document.getElementById("attendanceTableBody");
  const params = new URLSearchParams();
  const date = document.getElementById("attendanceDate").value;
  const person = document.getElementById("attendancePerson").value.trim();
  if (date) params.set("date", date);
  if (person) params.set("person", person);
  try {
    const response = await fetch(`/api/attendance?${params.toString()}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load attendance.");
    tableBody.replaceChildren();
    if (!result.rows.length) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="${currentRole === "admin" ? 5 : 4}">No attendance records found.</td>`;
      tableBody.appendChild(row);
      return;
    }
    result.rows.forEach((item) => {
      const row = document.createElement("tr");
      addTableCell(row, item.event_time.replace("T", " "));
      addTableCell(row, item.participant);
      addTableCell(row, item.event.replace("_", " "));
      addTableCell(row, item.operator);
      if (currentRole === "admin") {
        const actionCell = document.createElement("td");
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "small-button delete-button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteAttendanceRecord(item.id));
        actionCell.appendChild(deleteButton);
        row.appendChild(actionCell);
      }
      tableBody.appendChild(row);
    });
  } catch (error) {
    tableBody.textContent = error.message;
  }
}

async function deleteAttendanceRecord(recordId) {
  if (!window.confirm("Delete this attendance record? This cannot be undone.")) return;
  try {
    const response = await fetch(`/api/attendance/${recordId}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not delete attendance record.");
    await loadAttendance();
    await loadHomeAttendance();
  } catch (error) {
    window.alert(error.message);
  }
}

async function loadUsers() {
  const list = document.getElementById("userList");
  list.replaceChildren();
  try {
    const response = await fetch("/api/users");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load users.");
    result.users.forEach((user) => {
      const row = document.createElement("div");
      row.className = "quality-row";
      const label = document.createElement("strong");
      label.textContent = user.username;
      const role = document.createElement("span");
      role.textContent = user.role === "member" ? "Student/Employee" : user.role;
      const created = document.createElement("span");
      created.textContent = user.created_at.slice(0, 10);
      row.append(label, role, created);
      if (currentRole === "admin" && user.username !== currentUsername) {
        const resetButton = document.createElement("button");
        resetButton.type = "button";
        resetButton.className = "small-button";
        resetButton.textContent = "Reset password";
        resetButton.addEventListener("click", () => resetUserPassword(user.username));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "small-button delete-button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteUserAccount(user.username));
        row.append(resetButton, deleteButton);
      }
      list.appendChild(row);
    });
  } catch (error) {
    list.textContent = error.message;
  }
  await loadSignupRequests();
}

async function resetUserPassword(username) {
  const password = window.prompt(`Enter a new password for ${username} (at least 8 characters):`);
  if (password === null) return;
  const form = new FormData();
  form.append("password", password);
  try {
    const response = await fetch(`/api/users/${encodeURIComponent(username)}/password`, {
      method: "PUT",
      body: form,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not reset the password.");
    showMessage("userMessage", result.message, "success");
  } catch (error) {
    showMessage("userMessage", error.message, "error");
  }
}

async function deleteUserAccount(username) {
  if (!window.confirm(`Delete the account for ${username}? This cannot be undone.`)) return;
  try {
    const response = await fetch(`/api/users/${encodeURIComponent(username)}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not delete the account.");
    showMessage("userMessage", result.message, "success");
    await loadUsers();
  } catch (error) {
    showMessage("userMessage", error.message, "error");
  }
}

async function loadMonthlyReport() {
  const monthInput = document.getElementById("attendanceMonth");
  const tableBody = document.getElementById("monthlyAttendanceBody");
  const params = new URLSearchParams();
  if (monthInput.value) params.set("month", monthInput.value);
  try {
    const response = await fetch(`/api/attendance/monthly?${params.toString()}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load the monthly report.");
    if (!monthInput.value) monthInput.value = result.month;
    document.getElementById("monthlyPeopleAttended").textContent = result.people_attended;
    document.getElementById("monthlyCheckIns").textContent = result.total_check_ins;
    document.getElementById("monthlyCheckOuts").textContent = result.total_check_outs;
    document.getElementById("downloadMonthlyReport").href = `/api/attendance/monthly.csv?month=${encodeURIComponent(result.month)}`;
    tableBody.replaceChildren();
    if (!result.rows.length) {
      const row = document.createElement("tr");
      row.innerHTML = '<td colspan="6">No enrolled participants found.</td>';
      tableBody.appendChild(row);
      return;
    }
    result.rows.forEach((person) => {
      const row = document.createElement("tr");
      addTableCell(row, person.participant);
      addTableCell(row, person.category);
      addTableCell(row, person.days_attended);
      addTableCell(row, person.check_ins);
      addTableCell(row, person.check_outs);
      addTableCell(row, person.last_event === "—" ? "—" : person.last_event.replace("T", " "));
      tableBody.appendChild(row);
    });
  } catch (error) {
    tableBody.textContent = error.message;
  }
}

async function loadSignupRequests() {
  const list = document.getElementById("signupRequestList");
  if (!list) return;
  try {
    const response = await fetch("/api/signup-requests");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load signup requests.");
    list.replaceChildren();
    if (!result.requests.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No pending requests.";
      list.appendChild(empty);
      return;
    }
    result.requests.forEach((request) => {
      const row = document.createElement("div");
      row.className = "quality-row signup-request-row";
      const details = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = `${request.display_name} · ${request.category}`;
      const meta = document.createElement("small");
      meta.textContent = `ID: ${request.institution_id} · Username: ${request.username}`;
      details.append(name, meta);

      const matchingPeople = result.participants.filter((person) =>
        person.category === request.category &&
        person.name.trim().toLocaleLowerCase() === request.display_name.trim().toLocaleLowerCase()
      );
      const participantSelect = document.createElement("select");
      participantSelect.setAttribute("aria-label", `Enrolled profile for ${request.display_name}`);
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = matchingPeople.length ? "Choose matching profile" : "No matching profile";
      participantSelect.appendChild(placeholder);
      matchingPeople.forEach((person) => {
        const option = document.createElement("option");
        option.value = person.id;
        option.textContent = `${person.name} · ${person.category}`;
        participantSelect.appendChild(option);
      });

      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "small-button";
      approve.textContent = "Approve";
      approve.disabled = matchingPeople.length === 0;
      approve.addEventListener("click", () => approveSignupRequest(request, participantSelect.value));
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "small-button delete-button";
      reject.textContent = "Reject";
      reject.addEventListener("click", () => rejectSignupRequest(request));
      row.append(details, participantSelect, approve, reject);
      list.appendChild(row);
    });
  } catch (error) {
    list.textContent = error.message;
  }
}

async function approveSignupRequest(request, participantId) {
  if (!participantId) {
    window.alert("Choose the matching enrolled person first.");
    return;
  }
  if (!window.confirm(`Approve ${request.display_name}? Verify the student/employee ID first.`)) return;
  const form = new FormData();
  form.append("participant_id", participantId);
  try {
    const response = await fetch(`/api/signup-requests/${request.id}/approve`, { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not approve the account.");
    showMessage("userMessage", result.message, "success");
    await loadUsers();
  } catch (error) {
    window.alert(error.message);
  }
}

async function rejectSignupRequest(request) {
  if (!window.confirm(`Reject the account request from ${request.display_name}?`)) return;
  try {
    const response = await fetch(`/api/signup-requests/${request.id}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not reject the request.");
    await loadSignupRequests();
  } catch (error) {
    window.alert(error.message);
  }
}

document.getElementById("createUserButton").addEventListener("click", async () => {
  const form = new FormData();
  form.append("username", document.getElementById("newUsername").value.trim());
  form.append("password", document.getElementById("newUserPassword").value);
  form.append("role", document.getElementById("newUserRole").value);
  try {
    const response = await fetch("/api/users", { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not create account.");
    showMessage("userMessage", result.message, "success");
    document.getElementById("newUsername").value = "";
    document.getElementById("newUserPassword").value = "";
    await loadUsers();
  } catch (error) {
    showMessage("userMessage", error.message, "error");
  }
});

document.getElementById("refreshStatus").addEventListener("click", loadStatus);
document.getElementById("refreshHistory").addEventListener("click", loadHistory);
document.getElementById("refreshAttendance").addEventListener("click", loadAttendance);
document.getElementById("refreshMonthlyReport").addEventListener("click", loadMonthlyReport);
document.getElementById("attendanceMonth").addEventListener("change", loadMonthlyReport);
document.getElementById("refreshHomeAttendance").addEventListener("click", loadHomeAttendance);
document.getElementById("refreshAccess").addEventListener("click", loadAccess);
document.getElementById("refreshVisitorPasses").addEventListener("click", loadVisitorPasses);
document.getElementById("enrollVisitorShortcut").addEventListener("click", () => {
  document.getElementById("participantCategory").value = "Visitor";
  openPage("enroll");
});
document.getElementById("createVisitorPass").addEventListener("click", createVisitorPass);
document.getElementById("attendanceDate").addEventListener("change", loadAttendance);
document.getElementById("attendancePerson").addEventListener("input", loadAttendance);

loadStatus();
loadHomeAttendance();
