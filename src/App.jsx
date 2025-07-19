import { useState } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [extractedData, setExtractedData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [copyButtonText, setCopyButtonText] = useState('Copy as JSON');
  const [isDragOver, setIsDragOver] = useState(false);

  const handleFileChange = (event) => {
    setSelectedFiles(Array.from(event.target.files));
    // Reset previous results when new files are chosen
    setExtractedData(null);
    setError(null);
  };

  const handleFilesSelection = (files, shouldAdd = false) => {
    // Filter files to only accept supported formats
    const supportedFiles = Array.from(files).filter(file => {
      const validTypes = ['image/jpeg', 'image/jpg', 'image/png', 'application/pdf'];
      return validTypes.includes(file.type);
    });
    
    // Check for duplicates when adding files
    let finalFiles = supportedFiles;
    if (shouldAdd && selectedFiles.length > 0) {
      const existingNames = selectedFiles.map(f => f.name);
      const newFiles = supportedFiles.filter(file => !existingNames.includes(file.name));
      finalFiles = [...selectedFiles, ...newFiles];
      
      if (newFiles.length !== supportedFiles.length) {
        setError('Some files were skipped because they were already selected or unsupported.');
      }
    } else if (supportedFiles.length !== files.length) {
      setError('Some files were skipped. Only JPG, PNG, and PDF files are supported.');
    }
    
    setSelectedFiles(finalFiles);
    setExtractedData(null);
    if (finalFiles.length > 0) {
      setError(null);
    }
  };

  const handleFileDelete = (indexToDelete) => {
    const updatedFiles = selectedFiles.filter((_, index) => index !== indexToDelete);
    setSelectedFiles(updatedFiles);
    setExtractedData(null);
    setError(null);
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (event) => {
    event.preventDefault();
    // Only set drag over to false if we're leaving the main container
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setIsDragOver(false);
    }
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragOver(false);
    const files = event.dataTransfer.files;
    const shouldAdd = selectedFiles.length > 0;
    handleFilesSelection(files, shouldAdd);
  };

  const handleFileInputChange = (event) => {
    const shouldAdd = selectedFiles.length > 0;
    handleFilesSelection(event.target.files, shouldAdd);
    // Reset the input value so same file can be selected again if needed
    event.target.value = '';
  };

  const handleAddMoreFiles = () => {
    document.getElementById('addMoreFilePicker').click();
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setError('Please select at least one file first');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      if (selectedFiles.length === 1) {
        // Single file upload
        const formData = new FormData();
        formData.append('file', selectedFiles[0]);

        const response = await axios.post("http://127.0.0.1:8000/extract-details/", formData);
        
        // Check if the response has the expected structure
        if (response.data && response.data.success && response.data.data) {
          setExtractedData(response.data.data);
        } else {
          setError("Invalid response from API.");
        }
      } else {
        // Multiple files upload
        const formData = new FormData();
        selectedFiles.forEach(file => {
          formData.append('files', file);
        });

        const response = await axios.post("http://127.0.0.1:8000/bulk-extract/", formData, {
          responseType: 'blob', // Important for file download
          headers: {
            'Content-Type': 'multipart/form-data',
          }
        });

        // Create download link for CSV file
        const blob = new Blob([response.data], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', 'invoice_extraction_results.csv');
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);

        setExtractedData({ message: `Processed ${selectedFiles.length} files. CSV downloaded.` });
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
    <main 
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Full page drag overlay */}
      {isDragOver && (
        <div className="page-drag-overlay">
          <div className="page-drag-content">
            <div className="drop-icon">📁</div>
            <div className="drop-text">
              {selectedFiles.length > 0 ? 'Drop files to add them' : 'Drop files here'}
            </div>
          </div>
        </div>
      )}

      <h1>Invoice Extractor 📄</h1>
      
      <div className="upload-container">
        {selectedFiles.length === 0 ? (
          // Show simple browse button when no files are selected
          <>
            <span>Upload invoice(s) - Drag & drop files anywhere or click to browse (JPG, PNG, PDF):</span>
            <button 
              className="browse-btn"
              onClick={() => document.getElementById('filePicker').click()}
            >
              📁 Browse Files
            </button>
            <input
              id="filePicker"
              type="file"
              accept=".jpg,.jpeg,.png,.pdf"
              multiple
              onChange={handleFileInputChange}
            />
          </>
        ) : (
          // Show compact file list with add button when files are selected
          <div className="files-section">
            <div className="files-header">
              <span>Selected Files ({selectedFiles.length}):</span>
              <button className="add-files-btn" onClick={handleAddMoreFiles} title="Add more files">
                + Add More
              </button>
            </div>

            <div className="compact-file-list">
              {selectedFiles.map((file, index) => (
                <div key={index} className="file-item">
                  <div className="file-info">
                    <span className="file-icon">
                      {file.type.includes('pdf') ? '📄' : '🖼️'}
                    </span>
                    <span className="file-name">{file.name}</span>
                    <span className="file-size">
                      ({(file.size / 1024 / 1024).toFixed(2)} MB)
                    </span>
                  </div>
                  <button 
                    className="delete-btn" 
                    onClick={() => handleFileDelete(index)}
                    title="Remove file"
                  >
                    ❌
                  </button>
                </div>
              ))}
            </div>

            <input
              id="addMoreFilePicker"
              type="file"
              accept=".jpg,.jpeg,.png,.pdf"
              multiple
              onChange={handleFileInputChange}
            />
          </div>
        )}

        {selectedFiles.length > 0 && (
          <button className="btn" onClick={handleUpload} disabled={isLoading}>
            {isLoading 
              ? 'Processing...' 
              : selectedFiles.length === 1 
                ? 'Extract Details' 
                : 'Extract All & Download CSV'
            }
          </button>
        )}

        {/* Bootstrap-style Alerts - positioned right after the button */}
        {error && (
          <div className="alert alert-danger alert-dismissible" role="alert">
            <span><strong>Error!</strong> {error}</span>
            <button 
              type="button" 
              className="btn-close" 
              onClick={() => setError(null)}
              aria-label="Close"
            >
              ×
            </button>
          </div>
        )}

        {extractedData && extractedData.message && (
          <div className="alert alert-success alert-dismissible" role="alert">
            <span><strong>Success!</strong> {extractedData.message}</span>
            <button 
              type="button" 
              className="btn-close" 
              onClick={() => setExtractedData(null)}
              aria-label="Close"
            >
              ×
            </button>
          </div>
        )}
      </div>
      
      {/* Results Section - moved outside upload container */}
      {extractedData && selectedFiles.length === 1 && typeof extractedData === 'object' && !extractedData.message && (
        <div className="results-container">
          <div className="results-header">
            <div>
              <div className="results-title">Extracted Details</div>
              <div className="results-filename">{selectedFiles[0]?.name}</div>
            </div>
            <div className="copy-button-container">
              <button className="btn-copy-modern" onClick={handleCopyJson}>
                Copy
              </button>
            </div>
          </div>
          <div className="results-content">
            <div className="details-grid">
              {Object.entries(extractedData).map(([key, value]) => (
                <div key={key} className="detail-item">
                  <span className="detail-key">{key.replace(/_/g, ' ')}</span>
                  <span className="detail-value">{String(value)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Removed duplicate success message since it's now handled by the alert above */}
    </main>
  );
}

export default App;
