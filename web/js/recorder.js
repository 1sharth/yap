/**
 * In-browser microphone recorder & real-time visualizer
 */

class VoiceRecorder {
  constructor(canvasElement, onTick, onComplete) {
    this.canvas = canvasElement;
    this.canvasCtx = canvasElement ? canvasElement.getContext("2d") : null;
    this.onTick = onTick;
    this.onComplete = onComplete;

    this.mediaRecorder = null;
    this.audioChunks = [];
    this.stream = null;
    this.audioContext = null;
    this.analyser = null;
    this.animFrameId = null;

    this.startTime = null;
    this.timerInterval = null;
    this.isRecording = false;
  }

  async start() {
    if (this.isRecording) return;

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (err) {
      throw new Error(`Microphone access error: ${err.message}`);
    }

    // Determine supported mime type
    let mimeType = "audio/webm;codecs=opus";
    if (!MediaRecorder.isTypeSupported(mimeType)) {
      if (MediaRecorder.isTypeSupported("audio/webm")) {
        mimeType = "audio/webm";
      } else if (MediaRecorder.isTypeSupported("audio/ogg")) {
        mimeType = "audio/ogg";
      } else {
        mimeType = "";
      }
    }

    const options = mimeType ? { mimeType } : {};
    this.mediaRecorder = new MediaRecorder(this.stream, options);
    this.audioChunks = [];

    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        this.audioChunks.push(e.data);
      }
    };

    this.mediaRecorder.onstop = () => {
      const blob = new Blob(this.audioChunks, { type: mimeType || "audio/wav" });
      this.audioChunks = [];
      this._cleanupAudioNodes();
      if (this.onComplete) {
        this.onComplete(blob);
      }
    };

    // Audio Visualizer setup
    this._setupVisualizer();

    this.mediaRecorder.start(250); // Slice every 250ms
    this.isRecording = true;
    this.startTime = Date.now();

    // Timer interval
    this.timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
      const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
      const secs = String(elapsed % 60).padStart(2, "0");
      if (this.onTick) this.onTick(`${mins}:${secs}`);
    }, 200);
  }

  stop() {
    if (!this.isRecording) return;
    this.isRecording = false;
    clearInterval(this.timerInterval);

    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      this.mediaRecorder.stop();
    }
  }

  _setupVisualizer() {
    if (!this.canvas || !this.canvasCtx) return;

    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.audioContext = new AudioCtx();
      const source = this.audioContext.createMediaStreamSource(this.stream);
      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 64;
      source.connect(this.analyser);

      const bufferLength = this.analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      const { width, height } = this.canvas;

      const draw = () => {
        if (!this.isRecording) {
          this.canvasCtx.clearRect(0, 0, width, height);
          return;
        }

        this.animFrameId = requestAnimationFrame(draw);
        this.analyser.getByteFrequencyData(dataArray);

        this.canvasCtx.clearRect(0, 0, width, height);
        const barWidth = (width / bufferLength) * 1.5;
        let x = 0;

        for (let i = 0; i < bufferLength; i++) {
          const barHeight = (dataArray[i] / 255) * height;
          this.canvasCtx.fillStyle = `rgba(99, 102, 241, ${0.4 + (dataArray[i] / 255) * 0.6})`;
          this.canvasCtx.fillRect(x, height - barHeight, barWidth - 1, barHeight);
          x += barWidth;
        }
      };

      draw();
    } catch (e) {
      console.warn("Visualizer failed to init:", e);
    }
  }

  _cleanupAudioNodes() {
    if (this.animFrameId) cancelAnimationFrame(this.animFrameId);
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }
    if (this.audioContext && this.audioContext.state !== "closed") {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    if (this.canvas && this.canvasCtx) {
      this.canvasCtx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
  }
}

window.VoiceRecorder = VoiceRecorder;
