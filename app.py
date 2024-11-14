import streamlit as st
import tensorflow as tf
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image
import numpy as np
import plotly.graph_objects as go
import cv2
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Dropout, Flatten
from tensorflow.keras.optimizers import Adamax
from tensorflow.keras.metrics import Precision, Recall
import google.generativeai as genai
import PIL.Image
import os
from dotenv import load_dotenv
load_dotenv()

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

output_dir = 'saliency_maps'
os.makedirs(output_dir, exist_ok=True)

# Function to generate the initial explanation based on prediction
def generate_explanation(img_path, model_prediction, confidence):
    prompt = f"""
    You are an expert neurologist interpreting a saliency map for a brain MRI scan, generated by a deep learning model trained to classify brain tumors into categories: glioma, meningioma, pituitary, or no tumor.

    The model predicted the scan to be of class '{model_prediction}' with a confidence level of {confidence * 100}%.

    Context for response:
    - Describe specific regions of the brain highlighted in light cyan where the model focused to arrive at this prediction.
      For example, reference specific lobes, sulci, or anatomical structures if they appear relevant.
    - Discuss how the highlighted regions correlate with typical tumor presentation of the predicted class. For instance,
      explain if the distribution, shape, or spread of these highlighted areas aligns with known patterns for {model_prediction}.
    - Provide insights into the reasoning process, considering the neural features that might influence the model's focus.
      For example, mention if it’s emphasizing regions of abnormal density, asymmetry, or unusual boundaries that are often seen in {model_prediction} cases.
    - Avoid generic statements, and aim to make each sentence contribute a unique insight into why the model likely made this prediction
      based on the observed highlights.
    - Maintain clarity and precision in describing the anatomical and functional relevance of the model’s focus points on the MRI scan.

    Based on the above context, answer the following question from the user in a detailed yet concise manner.
    """
    img = PIL.Image.open(img_path)
    model = genai.GenerativeModel(model_name="gemini-1.5-flash")
    response = model.generate_content([prompt, img])
    return response.text

# Function to generate chat responses based on user questions
def generate_neurology_chat_response(model, img, user_query, model_prediction, confidence):
    prompt = f"""
    You are an expert neurologist interpreting a saliency map for a brain MRI scan, generated by a deep learning model trained to classify brain tumors into categories: glioma, meningioma, pituitary, or no tumor.

    The model has classified this MRI scan as '{model_prediction}' with a confidence level of {confidence * 100}%.

    When responding, please:
    - Answer questions directly based on the provided MRI scan classification and the highlighted regions in the saliency map.
    - Focus on explaining why the model might have classified the tumor as {model_prediction}, including anatomical and structural details relevant to this tumor type.
    - Describe any relevant characteristics of the {model_prediction} tumor type, such as typical regions affected, common shapes, or patterns in MRI imaging.
    - Avoid disclaimers about being an AI and instead focus on providing educational information relevant to this classification.

    Now, based on the model's classification and the saliency map, answer the following user question:

    "{user_query}"
    """
    response = model.generate_content([prompt, img])
    return response.text

def generate_saliency_map(model, img, img_array, class_index, img_size, uploaded_file):
    with tf.GradientTape() as tape:
        img_tensor = tf.convert_to_tensor(img_array)
        tape.watch(img_tensor)
        predictions = model(img_tensor)
        target_class = predictions[:, class_index]

    gradients = tape.gradient(target_class, img_tensor)
    gradients = tf.math.abs(gradients)
    gradients = tf.reduce_max(gradients, axis=-1)
    gradients = gradients.numpy().squeeze()

    # Resize gradients to match original image size
    gradients = cv2.resize(gradients, img_size)

    # Create a circular mask for the brain area
    center = (gradients.shape[0] // 2, gradients.shape[1] // 2)
    radius = min(center[0], center[1]) - 10
    y, x = np.ogrid[:gradients.shape[0], :gradients.shape[1]]
    mask = (x - center[0])**2 + (y - center[1])**2 <= radius**2

    # Apply mask to gradients
    gradients = gradients * mask

    # Normalize only the brain area
    brain_gradients = gradients[mask]
    if brain_gradients.max() > brain_gradients.min():
        brain_gradients = (brain_gradients - brain_gradients.min()) / (brain_gradients.max() - brain_gradients.min())
    gradients[mask] = brain_gradients

    # Apply a higher threshold
    threshold = np.percentile(gradients[mask], 80)
    gradients[gradients < threshold] = 0

    # Apply more aggressive smoothing
    gradients = cv2.GaussianBlur(gradients, (11, 11), 0)

    # Create a heatmap overlay with enhanced contrast
    heatmap = cv2.applyColorMap(np.uint8(255 * gradients), cv2.COLORMAP_JET)
    heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)

    # Resize heatmap to match original image size
    heatmap = cv2.resize(heatmap, img_size)
    # Superimpose the heatmap on original image with increased opacity
    original_img = image.img_to_array(img)
    superimposed_img = heatmap * 0.7 + original_img * 0.3
    superimposed_img = superimposed_img.astype(np.uint8)

    img_path = os.path.join(output_dir, uploaded_file.name)
    with open(img_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    saliency_map_path = f'saliency_maps/{uploaded_file.name}'

    # Save the saliency map
    cv2.imwrite(saliency_map_path, cv2.cvtColor(superimposed_img, cv2.COLOR_RGB2BGR))

    return superimposed_img


def load_xception_model(model_path):
    img_shape=(299,299,3)
    base_model = tf.keras.applications.Xception(include_top=False, weights="imagenet",
                                                input_shape=img_shape, pooling='max')

    model = Sequential([
        base_model,
        Flatten(),
        Dropout(rate=0.3),
        Dense(128, activation='relu'),
        Dropout(rate=0.25),
        Dense(4, activation='softmax')
    ])

    model.build((None,) + img_shape)

    # Compile the model
    model.compile(Adamax(learning_rate=0.001),
                  loss='categorical_crossentropy',
                  metrics=['accuracy',
                           Precision(),
                           Recall()])

    model.load_weights(model_path)

    return model

# Main Streamlit app with tabbed layout
st.title("Brain Tumor Classification")

# Tabs for different features
tabs = st.tabs(["Single Prediction", "Model Comparison"])

# Common functionality for each tab
def display_tab_content(tab_key):
    st.write("Upload an image of a brain MRI to classify the tumor type.")
    uploaded_file = st.file_uploader("Choose an image...", type=["jpg", "jpeg", "png"], key=tab_key)

    if uploaded_file is not None:
        selected_model = st.radio("Select Model", ("Transfer Learning - Xception", "Custom CNN"), key=f"{tab_key}_model")

        if selected_model == "Transfer Learning - Xception":
            model = load_xception_model('/content/xception_model.weights.h5')
            img_size = (299, 299)
        else:
            model = load_model('/content/cnn_model.h5')
            img_size = (224, 224)

        labels = ['Glioma', 'Meningioma', 'No tumor', 'Pituitary']
        img = image.load_img(uploaded_file, target_size=img_size)
        img_array = image.img_to_array(img)
        img_array = np.expand_dims(img_array, axis=0) / 255.0

        prediction = model.predict(img_array)
        class_index = np.argmax(prediction[0])
        result = labels[class_index]

        st.write(f"Predicted Class: {result}")
        st.write("Predictions")
        for label, prob in zip(labels, prediction[0]):
            st.write(f"{label}: {prob:.4f}")

        # Saliency map
        saliency_map = generate_saliency_map(model, img, img_array, class_index, img_size, uploaded_file)
        col1, col2 = st.columns(2)
        with col1:
            st.image(uploaded_file, caption='Uploaded Image', use_column_width=True)
        with col2:
            st.image(saliency_map, caption='Saliency Map', use_column_width=True)

        # Explanation
        saliency_map_path = f'saliency_maps/{uploaded_file.name}'
        explanation = generate_explanation(saliency_map_path, result, prediction[0][class_index])
        st.write("### Explanation")
        st.write(explanation)

        # Chat with the MRI feature
        st.write("### Chat with the MRI Image")
        st.write("Ask questions about the MRI scan to the multimodal LLM.")
        user_query = st.text_input("Your question about the MRI scan:", key=f"{tab_key}_query")

        if user_query:
            response_text = generate_neurology_chat_response(
                model=genai.GenerativeModel(model_name="gemini-1.5-flash"),
                img=img,
                user_query=user_query,
                model_prediction=result,
                confidence=prediction[0][class_index]
            )
            st.write("### Model's Response:")
            st.write(response_text)

# Single Prediction Tab
with tabs[0]:
    display_tab_content("single_prediction")

# Model Comparison Tab
with tabs[1]:
    display_tab_content("model_comparison")
