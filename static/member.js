const attendanceDate = document.getElementById("memberAttendanceDate");
const attendanceStatus = document.getElementById("memberAttendanceStatus");
const attendanceNotice = document.getElementById("memberAttendanceNotice");
const attendanceRows = document.getElementById("memberAttendanceRows");

function addMemberCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.appendChild(cell);
}

async function refreshMemberAttendance() {
  try {
    const response = await fetch("/api/my-attendance", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load your attendance.");
    attendanceDate.textContent = result.date;
    attendanceStatus.textContent = result.status;
    attendanceStatus.className = result.status === "Attended" ? "member-attended" : "member-not-attended";
    attendanceNotice.textContent = result.status === "Attended"
      ? `Attendance marked for ${result.date} at ${result.check_in_time.replace("T", " ")}.`
      : `No check-in has been recorded for you today (${result.date}).`;

    attendanceRows.replaceChildren();
    if (!result.records.length) {
      const row = document.createElement("tr");
      row.innerHTML = '<td colspan="2">No attendance records yet.</td>';
      attendanceRows.appendChild(row);
      return;
    }
    result.records.forEach((record) => {
      const row = document.createElement("tr");
      addMemberCell(row, record.event_time.replace("T", " "));
      addMemberCell(row, record.event.replace("_", " "));
      attendanceRows.appendChild(row);
    });
    document.getElementById("memberPageMessage").textContent = "";
  } catch (error) {
    document.getElementById("memberPageMessage").textContent = error.message;
  }
}

document.getElementById("refreshMemberAttendance").addEventListener("click", refreshMemberAttendance);
refreshMemberAttendance();
window.setInterval(refreshMemberAttendance, 30000);


const hostPassRows = document.getElementById("hostVisitorPassRows");
function addHostPassCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.appendChild(cell);
}
async function refreshHostVisitorPasses() {
  try {
    const response = await fetch("/api/visitor-passes", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load visitor requests.");
    hostPassRows.replaceChildren();
    if (!result.passes.length) {
      const row = document.createElement("tr"); const cell = document.createElement("td");
      cell.colSpan = 6; cell.textContent = "No visitor requests assigned to you."; row.appendChild(cell); hostPassRows.appendChild(row); return;
    }
    result.passes.forEach((pass) => {
      const row = document.createElement("tr");
      [pass.visitor_name, pass.purpose, `${pass.starts_at.replace("T", " ")} – ${pass.ends_at.replace("T", " ")}`, pass.status, pass.pass_code || "—"].forEach((value) => addHostPassCell(row, value));
      const actions = document.createElement("td");
      if (pass.status === "pending") {
        for (const decision of ["approved", "rejected"]) {
          const button = document.createElement("button"); button.type = "button"; button.className = decision === "approved" ? "small-button" : "small-button delete-button";
          button.textContent = decision === "approved" ? "Approve" : "Reject";
          button.addEventListener("click", () => decideHostVisitorPass(pass.id, decision)); actions.appendChild(button);
        }
      } else actions.textContent = "—";
      row.appendChild(actions); hostPassRows.appendChild(row);
    });
  } catch (error) {
    hostPassRows.textContent = error.message;
  }
}
async function decideHostVisitorPass(passId, decision) {
  const form = new FormData(); form.append("decision", decision);
  try {
    const response = await fetch(`/api/visitor-passes/${passId}/decision`, { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not update visitor request.");
    const notice = document.getElementById("memberPageMessage");
    notice.textContent = result.pass_code ? `${result.message} Pass code: ${result.pass_code}` : result.message;
    await refreshHostVisitorPasses();
  } catch (error) {
    document.getElementById("memberPageMessage").textContent = error.message;
  }
}
document.getElementById("refreshHostVisitorPasses").addEventListener("click", refreshHostVisitorPasses);
refreshHostVisitorPasses();
