import React, { useState, useEffect } from "react";
import "./index.css";

export default function App() {
  const [showSplash, setShowSplash] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setShowSplash(false), 3500);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="relative w-full h-screen bg-black overflow-hidden flex items-center justify-center text-white">
      <div className="holo-bg"></div>

      {showSplash ? (
        <div className="relative z-10 text-center animate-fadeIn">
          <div className="relative w-40 h-40 mx-auto mb-6">
            <div className="absolute inset-0 rounded-full bg-cyan-400 opacity-40 blur-3xl animate-pulse"></div>
            <div className="relative w-40 h-40 bg-gradient-to-tr from-cyan-400 to-blue-700 rounded-full animate-spin-slow shadow-[0_0_60px_20px_rgba(0,255,255,0.2)]"></div>
          </div>
          <h1 className="text-3xl font-bold tracking-[0.3em] text-cyan-300 mb-2">
            OMNINEWS 9.0
          </h1>
          <p className="text-cyan-500 text-sm">
            Initializing Neural Broadcast Systems...
          </p>
        </div>
      ) : (
        <div className="relative z-10 text-center animate-fadeIn">
          <h1 className="text-4xl font-extrabold text-cyan-400 mb-4 drop-shadow-[0_0_10px_rgba(0,255,255,0.4)]">
            OMNINEWS COMMAND CENTER
          </h1>
          <p className="text-gray-400 text-lg">
            Welcome to the future of real-time journalism.
          </p>
        </div>
      )}
    </div>
  );
}
