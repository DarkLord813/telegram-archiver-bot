// ==========================================
// 1. SETTINGS
// ==========================================
const SECRET_CODE = "FLASHXITS";
const CHANNELS = ["https://t.me/flashxits"];

// ==========================================
// 2. UNLOCK LOGIC
// ==========================================
function unlockSite() {
    const inputCode = document.getElementById('secret-code-input').value;
    
    if (inputCode === SECRET_CODE) {
        document.getElementById('lock-screen').classList.add('hidden');
        document.getElementById('main-content').classList.remove('hidden');
        resetUI();
    } else {
        alert("❌ Incorrect Secret Code! Make sure you joined the channel and got the right code.");
    }
}

// ==========================================
// 3. INITIAL SETUP
// ==========================================
window.onload = function() {
    const container = document.getElementById('channel-links-container');
    container.innerHTML = "";
    
    CHANNELS.forEach((link) => {
        const a = document.createElement('a');
        a.href = link;
        a.target = "_blank";
        a.className = "tg-btn";
        a.innerText = `1️⃣ Join Telegram Channel`;
        container.appendChild(a);
    });

    resetUI();
};

function resetUI() {
    document.getElementById('results-section').classList.add('hidden');
    document.getElementById('general').innerText = "0";
    document.getElementById('reddot').innerText = "0";
    document.getElementById('scope2x').innerText = "0";
    document.getElementById('scope4x').innerText = "0";
    document.getElementById('sniper').innerText = "0";
    document.getElementById('freelook').innerText = "0";
    document.getElementById('firebutton').innerText = "0%";
    document.getElementById('dpi').innerText = "0";
    document.getElementById('device-name').innerText = "YOUR DEVICE";
}

// ==========================================
// 4. GENERATOR LOGIC (FIXED RED DOT)
// ==========================================
function generateSensi() {
    const deviceName = document.getElementById('deviceInput').value;
    const ram = parseInt(document.getElementById('ramSelect').value);
    const deviceType = document.getElementById('deviceType').value;

    if (deviceName.trim() === "") {
        alert("Please enter your device name first!");
        return;
    }

    // 1. Unique Hash from Device Name
    let hash = 0;
    for (let i = 0; i < deviceName.length; i++) {
        const char = deviceName.charCodeAt(i);
        hash = (hash << 5) - hash + char;
        hash |= 0; 
    }
    let baseOffset = Math.abs(hash) % 30; 

    // 2. Base Values (Setting General HIGHER than Red Dot)
    let general = 105;
    let reddot = 80; // Starts lower than general
    let scope2x = 90;
    let scope4x = 75;
    let sniper = 50;
    let freelook = 70;
    let firebutton = 45;
    let dpi = 400;

    // 3. Apply Device Name specific offset
    // General gets the full offset, Red Dot gets 60% of it
    general += baseOffset;
    reddot += (baseOffset * 0.6);
    scope2x += (baseOffset * 0.8);
    scope4x += (baseOffset * 0.6);
    sniper += (baseOffset * 0.5);
    freelook += (baseOffset * 0.7);

    // 4. Adjust based on RAM
    if (ram <= 2) {
        general -= 10; reddot -= 15; scope2x -= 10; dpi = 300;
    } else if (ram >= 8) {
        general += 15; reddot += 5; scope2x += 10; dpi = 600;
    }

    // 5. Adjust based on Device Type (Refresh Rate)
    if (deviceType === "iphone") {
        general -= 5; reddot -= 5; dpi = 0; 
    } else if (deviceType === "iphone_pro") {
        general -= 10; reddot -= 10; dpi = 0;
    }

    // 6. Clamp values
    general = Math.min(150, Math.max(1, general));
    reddot = Math.min(150, Math.max(1, reddot));
    scope2x = Math.min(150, Math.max(1, scope2x));
    scope4x = Math.min(150, Math.max(1, scope4x));
    sniper = Math.min(100, Math.max(1, sniper));
    freelook = Math.min(100, Math.max(1, freelook));

    // 7. Update UI
    document.getElementById('device-name').innerText = deviceName.toUpperCase();
    document.getElementById('general').innerText = Math.round(general);
    document.getElementById('reddot').innerText = Math.round(reddot);
    document.getElementById('scope2x').innerText = Math.round(scope2x);
    document.getElementById('scope4x').innerText = Math.round(scope4x);
    document.getElementById('sniper').innerText = Math.round(sniper);
    document.getElementById('freelook').innerText = Math.round(freelook);
    document.getElementById('firebutton').innerText = Math.round(firebutton) + "%";
    
    const dpiElement = document.getElementById('dpi');
    if (dpi === 0) {
        dpiElement.innerText = "N/A (iOS)";
    } else {
        dpiElement.innerText = Math.round(dpi);
    }

    document.getElementById('results-section').classList.remove('hidden');
}