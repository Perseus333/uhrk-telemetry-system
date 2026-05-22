const rocket = document.getElementById("rocket-view");
let threeScene = null;
let lastOrientation = "";

const DEG = Math.PI / 180;

rocket.addEventListener("load", () => {
    // Bypass the orientation attribute (which unconditionally calls
    // arRenderer.onUpdateScene() and crashes when AR is not initialized).
    // Instead, grab the internal Three.js scene directly — this is the
    // officially-documented workaround from the model-viewer team.
    // See: https://github.com/google/model-viewer/discussions/1873
    const sceneSym = Object.getOwnPropertySymbols(rocket)
        .find(s => s.description === "scene");

    if (sceneSym) {
        threeScene = rocket[sceneSym];
        console.log("Three.js scene acquired:", threeScene);
    } else {
        console.error("Could not find $scene symbol — check model-viewer version.");
    }
});

function updateRocketOrientation(stage) {
    if (stage.roll == null || stage.pitch == null || stage.yaw == null) return;
    if (!threeScene?.pivot) return;

    const roll  =  stage.roll;
    const pitch = -stage.pitch;   // keep your existing negation
    const yaw   =  stage.yaw;

    const key = `${roll.toFixed(2)} ${pitch.toFixed(2)} ${yaw.toFixed(2)}`;
    if (key === lastOrientation) return;
    lastOrientation = key;

    // model-viewer's orientation order is: yaw → Y-axis, then pitch → local X,
    // then roll → local Z.  In Three.js Euler terms that is order 'YXZ'.
    rocket[Object.getOwnPropertySymbols(rocket)
        .find(s => s.description === "scene")]
        ?.pivot
        ?.rotation
        .set(pitch * DEG, yaw * DEG, roll * DEG, 'YXZ');

    // Tell model-viewer to repaint on the next frame.
    threeScene.queueRender();
}

async function pollTelemetry() {
    try {
        const response = await fetch("./telemetry_latest.json", { cache: "no-store" });
        const data = await response.json();
        if (data.stages?.length) {
            updateRocketOrientation(data.stages[0]);
        }
    } catch (err) {
        console.error(err);
    }
}

setInterval(pollTelemetry, 1000);