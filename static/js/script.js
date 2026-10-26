
function searchServices() {
    const locationInput = document.querySelector("#locationInput");
    const serviceInput = document.querySelector("#serviceInput");

    const city = locationInput.value.trim();
    const service = serviceInput.value.trim();

    if (!city) {
        alert("Please enter your city or location.");
        locationInput.focus();
        return;
    }

    let url = "/explore?city=" + encodeURIComponent(city);

    if (service) {
        url += "&search=" + encodeURIComponent(service);
    }

    window.location.href = url;
}


// Login Password Toggle

const passwordInput = document.getElementById("password");
const togglePassword = document.getElementById("togglePassword");

if (passwordInput && togglePassword) {
    togglePassword.addEventListener("click", function () {
        if (passwordInput.type === "password") {
            passwordInput.type = "text";
            togglePassword.textContent = "🙈";
            togglePassword.setAttribute("aria-label", "Hide password");
        } else {
            passwordInput.type = "password";
            togglePassword.textContent = "👁";
            togglePassword.setAttribute("aria-label", "Show password");
        }
    });
}


// Other Password Toggles

const passwordToggles = document.querySelectorAll(".toggle-password");

passwordToggles.forEach(function (button) {
    button.addEventListener("click", function () {
        const targetId = button.getAttribute("data-target");
        const targetInput = document.getElementById(targetId);

        if (!targetInput) return;

        if (targetInput.type === "password") {
            targetInput.type = "text";
            button.textContent = "🙈";
            button.setAttribute("aria-label", "Hide password");
        } else {
            targetInput.type = "password";
            button.textContent = "👁";
            button.setAttribute("aria-label", "Show password");
        }
    });
});


// Provider Registration - Step 1

const providerForm = document.getElementById("providerStepOneForm");

if (providerForm) {
    providerForm.addEventListener("submit", function (event) {
        event.preventDefault();

        const businessName = document.getElementById("business_name");
        const category = document.getElementById("category");
        const mobile = document.getElementById("mobile");
        const email = document.getElementById("email");
        const message = document.getElementById("providerFormMessage");

        message.textContent = "";

        if (!businessName.value.trim()) {
            message.textContent = "Please enter your business name.";
            businessName.focus();
            return;
        }

        if (!category.value) {
            message.textContent = "Please select a service category.";
            category.focus();
            return;
        }

        if (!/^[6-9]\d{9}$/.test(mobile.value.trim())) {
            message.textContent = "Please enter a valid 10-digit phone number.";
            mobile.focus();
            return;
        }

        if (!email.value.trim() || !email.checkValidity()) {
            message.textContent = "Please enter a valid email address.";
            email.focus();
            return;
        }

        message.style.color = "#27845d";
        message.textContent =
            "Step 1 details are valid. Next step will be added after its design is ready.";
    });
}


// AI ASSISTANT CHAT - GEMINI API

const aiChatForm = document.getElementById("aiChatForm");

if (aiChatForm) {
    aiChatForm.addEventListener("submit", async function (event) {
        event.preventDefault();

        const input = document.getElementById("aiMessageInput");
        const chatBody = document.getElementById("chatBody");
        const message = input.value.trim();

        if (!message) return;

        // Display user message
        const userMessage = document.createElement("div");
        userMessage.className = "chat-message user-message";

        const userBubble = document.createElement("div");
        userBubble.className = "message-bubble";
        userBubble.textContent = message;

        userMessage.appendChild(userBubble);
        chatBody.appendChild(userMessage);

        input.value = "";

        // Display assistant loading message
        const assistantMessage = document.createElement("div");
        assistantMessage.className = "chat-message assistant-message";

        const avatar = document.createElement("div");
        avatar.className = "assistant-avatar";
        avatar.innerHTML = '<i class="fas fa-globe-asia"></i>';

        const assistantBubble = document.createElement("div");
        assistantBubble.className = "message-bubble assistant-bubble";
        assistantBubble.textContent = "Thinking...";

        assistantMessage.appendChild(avatar);
        assistantMessage.appendChild(assistantBubble);
        chatBody.appendChild(assistantMessage);

        chatBody.scrollTop = chatBody.scrollHeight;

        try {
            const response = await fetch("/api/ai-chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    message: message
                })
            });

            const data = await response.json();

            if (!response.ok) {
                assistantBubble.textContent =
                    data.error || "Something went wrong.";
            } else {
                assistantBubble.textContent =
                    data.reply || "I couldn't generate a response.";
            }

        } catch (error) {
            console.error("AI Chat Error:", error);

            assistantBubble.textContent =
                "Unable to connect. Please try again.";
        }

        chatBody.scrollTop = chatBody.scrollHeight;
    });
}


// Add Service Form

document.addEventListener("DOMContentLoaded", function () {
    const addForm = document.getElementById("add-service");
    const openButton = document.getElementById("openAddForm");
    const emptyButton = document.getElementById("emptyAddButton");
    const sidebarButton = document.getElementById("sidebarAddService");
    const closeButton = document.getElementById("closeAddForm");

    function openAddForm(event) {
        if (event) event.preventDefault();

        if (!addForm) return;

        addForm.classList.add("show");

        addForm.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    }

    function closeAddForm() {
        if (!addForm) return;
        addForm.classList.remove("show");
    }

    if (openButton) {
        openButton.addEventListener("click", openAddForm);
    }

    if (emptyButton) {
        emptyButton.addEventListener("click", openAddForm);
    }

    if (sidebarButton) {
        sidebarButton.addEventListener("click", openAddForm);
    }

    if (closeButton) {
        closeButton.addEventListener("click", closeAddForm);
    }
});


// Admin Sidebar Active Link

document.addEventListener("DOMContentLoaded", function () {
    const navLinks = document.querySelectorAll(".admin-nav-link");

    navLinks.forEach(function (link) {
        link.addEventListener("click", function () {
            navLinks.forEach(function (item) {
                item.classList.remove("active");
            });

            if (link.getAttribute("href") &&
                link.getAttribute("href").includes("#")) {
                link.classList.add("active");
            }
        });
    });
});
// Emergency - Find Nearby Hospitals and Pharmacies


function findNearbyEmergency(type) {
    if (!navigator.geolocation) {
        alert("Your browser does not support location.");
        return;
    }

    navigator.geolocation.getCurrentPosition(
        function (position) {
            const latitude = position.coords.latitude;
            const longitude = position.coords.longitude;

            let mapsUrl;

            if (type === "hospital") {
                mapsUrl =
                    "https://www.google.com/maps/search/hospitals/@" +
                    latitude + "," + longitude + ",16z";
            } else if (type === "pharmacy") {
                mapsUrl =
                    "https://www.google.com/maps/search/pharmacy/@" +
                    latitude + "," + longitude + ",16z";
            } else {
                return;
            }

            console.log("Opening Maps URL:", mapsUrl);
            window.open(mapsUrl, "_blank");
        },
        function () {
            alert("Unable to get your current location. Please allow location access.");
        },
        {
            enableHighAccuracy: true,
            maximumAge: 0,
            timeout: 20000
        }
    );
}
