import React, { useState } from "react";
import axios from "axios";
import "./App.css";

function App() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [extractedData, setExtractedData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [copyButtonText, setCopyButtonText] = useState('Copy as JSON');

  const handleFileChange = (event) => {
    setSelectedFile(event.target.files[0]);
    // Reset previous results when a new file is chosen
    setExtractedData(null);
    setError(null);
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    setIsLoading(true);
    setExtractedData(null);
    setError(null);

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const response = await axios.post("http://127.0.0.1:8000/extract-details/", formData);
      
      const rawResponse = response.data.extracted_data;
      console.log("Raw response from AI:", rawResponse); // For debugging

      // --- ROBUST JSON PARSING ---
      // Find the start and end of the JSON object within the raw text
      const jsonStartIndex = rawResponse.indexOf('{');
      const jsonEndIndex = rawResponse.lastIndexOf('}');

      if (jsonStartIndex !== -1 && jsonEndIndex !== -1) {
        const jsonString = rawResponse.substring(jsonStartIndex, jsonEndIndex + 1);
        const parsedData = JSON.parse(jsonString);
        setExtractedData(parsedData);
      } else {
        // If no '{' or '}' is found, the response is not what we expected.
        setError("The AI did not return a valid data object.");
      }

    } catch (err) {
      const errorMessage = err.response?.data?.detail || "An error occurred during extraction.";
      setError(errorMessage);
      console.error("Upload Error:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopyJson = () => {
    if (!extractedData) return;

    // Format the data into a nicely indented JSON string
    const jsonString = JSON.stringify(extractedData, null, 2);

    // Use the modern Clipboard API
    navigator.clipboard.writeText(jsonString).then(() => {
      // Provide feedback to the user
      setCopyButtonText('Copied!');
      // Reset the button text after 2 seconds
      setTimeout(() => {
        setCopyButtonText('Copy as JSON');
      }, 2000);
    }).catch(err => {
      console.error('Failed to copy: ', err);
      alert('Could not copy text to clipboard.');
    });
  };

  return (
    <main>
      <h1>Invoice Extractor 📄</h1>
      <span>Upload an invoice (jpg, png, pdf):</span>

      <label htmlFor="filePicker" className="custom-filePicker">
        {selectedFile ? selectedFile.name : 'Choose a file'}
      </label>
      <input
        id="filePicker"
        type="file"
        accept=".jpg,.jpeg,.png,.pdf"
        onChange={handleFileChange}
      />

      <button className="btn" onClick={handleUpload} disabled={!selectedFile || isLoading}>
        {isLoading ? 'Extracting...' : 'Extract Details'}
      </button>
      
      {/* --- Results Section --- */}

      {error && (
        <div className="error-container">
          <p><strong>Error:</strong> {error}</p>
        </div>
      )}

      {extractedData && (
        <div className="results-container">
          <h2>Extracted Details</h2>
          <div className="details-grid">
            {Object.entries(extractedData).map(([key, value]) => (
              <div key={key} className="detail-item">
                <span className="detail-key">{key.replace(/_/g, ' ')}:</span>
                <span className="detail-value">{String(value)}</span>
              </div>
            ))}
          </div>
          <button className="btn-copy" onClick={handleCopyJson}>
            {copyButtonText}
          </button>
        </div>
      )}
    </main>
  );
}

export default App;
