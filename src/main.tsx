import React from 'react';
import ReactDOM from 'react-dom/client';
import 'leaflet/dist/leaflet.css';
import './styles.css';
import App from './App';
import { startUiScaleSync } from './uiScale';

// --ui-scale 을 화면 폭에 맞춰 계속 갱신한다. 렌더 전에 한 번 적용해야
// 첫 화면이 기본값으로 그려졌다가 튀는 것을 막는다.
startUiScaleSync();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
