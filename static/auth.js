document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input) return;
    const showing = input.type === "password";
    input.type = showing ? "text" : "password";
    const label = showing ? "Hide password" : "Show password";
    button.setAttribute("aria-label", label);
    button.title = label;
  });
});
