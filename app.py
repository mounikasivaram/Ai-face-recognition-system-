import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
APP_DATA_DIR = Path(os.environ.get("APP_DATA_DIR", str(ROOT))).expanduser().resolve()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = APP_DATA_DIR / "faces"
MODEL_DIR = APP_DATA_DIR / "model"
MODEL_PATH = MODEL_DIR / "lbph_model.yml"
LABELS_PATH = MODEL_DIR / "labels.json"
CASCADE_PATH = ROOT / "haarcascade_frontalface_default.xml"
CASCADE_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/"
    "5.x/data/haarcascades/haarcascade_frontalface_default.xml"
)
IMAGE_SIZE = (200, 200)
MIN_IMAGES_PER_PERSON = 3


def get_detector():
    if not CASCADE_PATH.exists():
        print("Downloading the OpenCV face detector...")
        try:
            with urlopen(CASCADE_URL, timeout=30) as response:
                CASCADE_PATH.write_bytes(response.read())
        except (URLError, OSError) as error:
            raise RuntimeError(
                "Could not download the face detector. Check your internet "
                "connection and try again."
            ) from error

    detector = cv2.CascadeClassifier(str(CASCADE_PATH))
    if detector.empty():
        raise RuntimeError(
            f"Could not load the face detector: {CASCADE_PATH}"
        )
    return detector


def clean_name(name):
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    if not name:
        raise ValueError("Use a name containing letters or numbers.")
    return name


def capture_faces(name, camera_index):
    person_folder = DATA_DIR / name
    person_folder.mkdir(parents=True, exist_ok=True)

    detector = get_detector()
    camera = cv2.VideoCapture(camera_index)
    if not camera.isOpened():
        raise RuntimeError(
            f"Could not open camera {camera_index}. Try --camera 1."
        )

    saved = 0
    print("Press C to save a face image. Press Q to finish.")

    try:
        while True:
            success, frame = camera.read()
            if not success:
                print("Could not read a frame from the webcam.")
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(
                gray,
                scaleFactor=1.2,
                minNeighbors=5,
                minSize=(60, 60),
            )

            face_box = None
            if len(faces) > 0:
                face_box = max(faces, key=lambda box: box[2] * box[3])
                x, y, width, height = face_box
                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + width, y + height),
                    (0, 255, 0),
                    2,
                )
                message = "C: save image | Q: finish"
                color = (0, 255, 0)
            else:
                message = "No face detected"
                color = (0, 0, 255)

            cv2.putText(
                frame,
                message,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
            )
            cv2.imshow("Face enrollment", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

            if key == ord("c") and face_box is not None:
                x, y, width, height = face_box
                face = gray[y:y + height, x:x + width]
                face = cv2.resize(face, IMAGE_SIZE)

                existing_images = list(person_folder.glob("*.png"))
                image_path = person_folder / f"{len(existing_images) + 1:04d}.png"
                cv2.imwrite(str(image_path), face)
                saved += 1
                print(f"Saved {image_path.name}")

    finally:
        camera.release()
        cv2.destroyAllWindows()

    print(f"Saved {saved} image(s) for {name}.")
    print("Tip: capture at least 3 images per participant; 4 varied images are recommended.")


def train_model():
    if not DATA_DIR.exists():
        raise RuntimeError(
            "No face images found. Run the capture command first."
        )

    people = sorted(
        folder for folder in DATA_DIR.iterdir() if folder.is_dir()
    )
    if len(people) < 2:
        raise RuntimeError(
            "Capture images for at least two participants before training."
        )

    images = []
    labels = []
    names = {}

    for label_id, folder in enumerate(people):
        names[str(label_id)] = folder.name
        image_count = 0

        for image_path in sorted(folder.glob("*.png")):
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                images.append(cv2.resize(image, IMAGE_SIZE))
                labels.append(label_id)
                image_count += 1

        print(f"{folder.name}: loaded {image_count} image(s)")

        if image_count < MIN_IMAGES_PER_PERSON:
            raise RuntimeError(
                f"{folder.name} has {image_count} readable image(s). "
                f"Capture at least {MIN_IMAGES_PER_PERSON} images for every participant before training."
            )

    if len(set(labels)) < 2:
        raise RuntimeError(
            "Need readable face images for at least two participants."
        )

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, np.array(labels, dtype=np.int32))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    recognizer.write(str(MODEL_PATH))
    LABELS_PATH.write_text(
        json.dumps(names, indent=2),
        encoding="utf-8",
    )

    print(f"Training complete. Model saved in: {MODEL_DIR}")


def recognize_faces(camera_index, threshold):
    if not MODEL_PATH.exists() or not LABELS_PATH.exists():
        raise RuntimeError(
            "Train the model first by running: python app.py train"
        )

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(str(MODEL_PATH))
    names = json.loads(LABELS_PATH.read_text(encoding="utf-8"))

    detector = get_detector()
    camera = cv2.VideoCapture(camera_index)
    if not camera.isOpened():
        raise RuntimeError(
            f"Could not open camera {camera_index}. Try --camera 1."
        )

    print("Recognition started. Press Q in the video window to quit.")

    try:
        while True:
            success, frame = camera.read()
            if not success:
                print("Could not read a frame from the webcam.")
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(
                gray,
                scaleFactor=1.2,
                minNeighbors=5,
                minSize=(60, 60),
            )

            for (x, y, width, height) in faces:
                face = gray[y:y + height, x:x + width]
                face = cv2.resize(face, IMAGE_SIZE)

                label_id, distance = recognizer.predict(face)

                if distance <= threshold:
                    name = names.get(str(label_id), "Unknown")
                    color = (0, 255, 0)
                else:
                    name = "Unknown"
                    color = (0, 165, 255)

                caption = f"{name} ({distance:.1f})"
                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + width, y + height),
                    color,
                    2,
                )
                cv2.putText(
                    frame,
                    caption,
                    (x, max(25, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    color,
                    2,
                )

            cv2.imshow("Face recognition", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(
        description="Local webcam face-recognition demo"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    capture_command = commands.add_parser(
        "capture",
        help="Capture face images for one participant",
    )
    capture_command.add_argument("name")
    capture_command.add_argument("--camera", type=int, default=0)

    commands.add_parser("train", help="Train the recognition model")

    recognize_command = commands.add_parser(
        "recognize",
        help="Recognize enrolled participants",
    )
    recognize_command.add_argument("--camera", type=int, default=0)
    recognize_command.add_argument("--threshold", type=float, default=70.0)

    args = parser.parse_args()

    try:
        if args.command == "capture":
            capture_faces(clean_name(args.name), args.camera)
        elif args.command == "train":
            train_model()
        elif args.command == "recognize":
            recognize_faces(args.camera, args.threshold)
    except (RuntimeError, ValueError, cv2.error, URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
