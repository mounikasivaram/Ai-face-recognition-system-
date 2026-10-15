import json
from pathlib import Path

import cv2
import numpy as np
import streamlit as st

import app as face_app


st.set_page_config(
    page_title="AI Face Recognition",
    page_icon="👤",
    layout="wide",
)

st.title("AI Face Recognition Dashboard")
st.caption("A local classroom demo for people who agreed to enroll.")

# Read the images and model created by app.py
data_dir = face_app.DATA_DIR
model_ready = face_app.MODEL_PATH.exists() and face_app.LABELS_PATH.exists()

people = sorted(
    folder for folder in data_dir.iterdir() if folder.is_dir()
) if data_dir.exists() else []

image_count = sum(
    len(list(folder.glob("*.png")))
    for folder in people
)

page = st.sidebar.radio(
    "Project menu",
    ["Overview", "Enroll", "Train model", "Recognize"],
)

st.sidebar.caption("Face images and model files are stored in this project folder.")

# Summary cards stay visible on every page.
col1, col2, col3 = st.columns(3)
col1.metric("Enrolled participants", len(people))
col2.metric("Saved face images", image_count)
col3.metric("Model status", "Ready" if model_ready else "Not trained")


def decode_image(uploaded_file):
    """Convert a Streamlit camera/upload image into an OpenCV color image."""
    contents = np.frombuffer(uploaded_file.getvalue(), dtype=np.uint8)
    return cv2.imdecode(contents, cv2.IMREAD_COLOR)


def find_faces(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = face_app.get_detector()
    boxes = detector.detectMultiScale(
        gray,
        scaleFactor=1.2,
        minNeighbors=5,
        minSize=(60, 60),
    )
    return gray, boxes


if page == "Overview":
    st.subheader("System Dashboard")
    st.write(
        "This project detects faces with an OpenCV Haar cascade and compares "
        "them with enrolled face images using the LBPH recognizer."
    )

    st.markdown(
        """
        **How to use the dashboard**
        1. Open **Enroll** and take photos for each participant.
        2. Open **Train model** after enrolling at least two participants.
        3. Open **Recognize** and take a photo to check a face.
        """
    )
    st.info(
        "Capture at least 3 photos per participant; 4 varied photos are recommended. "
        "Use face images only with the participant’s permission."
    )


elif page == "Enroll":
    st.subheader("Enroll a participant")
    person_name = st.text_input("Participant label", placeholder="Example: Mounika")
    camera_photo = st.camera_input("Take a face photo", key="enroll_camera")

    if camera_photo is not None:
        frame = decode_image(camera_photo)
        gray, boxes = find_faces(frame)

        if len(boxes) == 0:
            st.image(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), caption="Captured photo")
            st.warning("No face detected. Try again with your face in view and good lighting.")
        else:
            x, y, width, height = max(boxes, key=lambda box: box[2] * box[3])
            preview = frame.copy()
            cv2.rectangle(
                preview,
                (x, y),
                (x + width, y + height),
                (0, 255, 0),
                2,
            )
            st.image(cv2.cvtColor(preview, cv2.COLOR_BGR2RGB), caption="Face detected")

            if st.button("Save this face image", type="primary"):
                try:
                    label = face_app.clean_name(person_name)
                    person_folder = data_dir / label
                    person_folder.mkdir(parents=True, exist_ok=True)

                    existing = [
                        int(path.stem)
                        for path in person_folder.glob("*.png")
                        if path.stem.isdigit()
                    ]
                    next_number = max(existing, default=0) + 1

                    face_crop = gray[y:y + height, x:x + width]
                    face_crop = cv2.resize(face_crop, face_app.IMAGE_SIZE)
                    output_path = person_folder / f"{next_number:04d}.png"
                    cv2.imwrite(str(output_path), face_crop)

                    st.success(f"Saved image {next_number} for {label}.")
                except ValueError as error:
                    st.error(str(error))


elif page == "Train model":
    st.subheader("Train the recognition model")
    st.write("Enroll at least two participants and capture at least 3 readable face images for each. Four varied images per person are recommended.")

    for folder in people:
        count = len(list(folder.glob("*.png")))
        st.write(f"**{folder.name}:** {count} image(s)")

    if st.button("Train model", type="primary"):
        try:
            face_app.train_model()
            st.success("Training complete. The model is ready to use.")
        except (RuntimeError, cv2.error) as error:
            st.error(str(error))


elif page == "Recognize":
    st.subheader("Recognize a face")
    threshold = st.slider(
        "Match threshold",
        min_value=30,
        max_value=120,
        value=70,
        help="Lower values are stricter. Adjust this if known faces show as Unknown.",
    )

    camera_photo = st.camera_input("Take a photo to recognize", key="recognize_camera")
    uploaded_photo = None

    if camera_photo is None:
        uploaded_photo = st.file_uploader(
            "Or choose a photo",
            type=["jpg", "jpeg", "png"],
        )

    photo = camera_photo if camera_photo is not None else uploaded_photo

    if photo is not None:
        frame = decode_image(photo)

        if st.button("Recognize face", type="primary"):
            if not model_ready:
                st.error("Train the model first from the Train model page.")
            else:
                try:
                    gray, boxes = find_faces(frame)

                    if len(boxes) == 0:
                        st.warning("No face detected in this photo.")
                    else:
                        recognizer = cv2.face.LBPHFaceRecognizer_create()
                        recognizer.read(str(face_app.MODEL_PATH))
                        labels = json.loads(
                            face_app.LABELS_PATH.read_text(encoding="utf-8")
                        )

                        results = []
                        for (x, y, width, height) in boxes:
                            crop = gray[y:y + height, x:x + width]
                            crop = cv2.resize(crop, face_app.IMAGE_SIZE)
                            label_id, distance = recognizer.predict(crop)

                            if distance <= threshold:
                                name = labels.get(str(label_id), "Unknown")
                                color = (0, 200, 0)
                            else:
                                name = "Unknown"
                                color = (0, 140, 255)

                            results.append(f"{name} (distance {distance:.1f})")
                            cv2.rectangle(
                                frame,
                                (x, y),
                                (x + width, y + height),
                                color,
                                2,
                            )
                            cv2.putText(
                                frame,
                                name,
                                (x, max(25, y - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                color,
                                2,
                            )

                        st.image(
                            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                            caption="Recognition result",
                        )
                        for result in results:
                            st.write(result)

                except (RuntimeError, cv2.error, OSError) as error:
                    st.error(str(error))
                

