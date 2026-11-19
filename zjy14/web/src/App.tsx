/** 路由与布局。 */

import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import CharactersPage from "./pages/CharactersPage";
import DashboardPage from "./pages/DashboardPage";
import DevicesPage from "./pages/DevicesPage";
import EmotionPage from "./pages/EmotionPage";
import HistoryPage from "./pages/HistoryPage";
import LogsPage from "./pages/LogsPage";
import SpeakersPage from "./pages/SpeakersPage";
import SynthesisPage from "./pages/SynthesisPage";
import SystemPage from "./pages/SystemPage";

const NAV = [
  { to: "/dashboard", label: "仪表盘" },
  { to: "/characters", label: "角色管理" },
  { to: "/speakers", label: "声音管理" },
  { to: "/emotion", label: "情绪管理" },
  { to: "/synthesis", label: "合成测试" },
  { to: "/history", label: "合成记录" },
  { to: "/devices", label: "设备管理" },
  { to: "/system", label: "系统设置" },
  { to: "/logs", label: "日志中心" },
];

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <h1>zjy14</h1>
          <p>Custom Voice Engine</p>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          CosyVoice3 · RTX 5070
          <br />
          路线乙（无情绪控制）
          <br />
          <em>愿群星为你闪耀，百花为你绽放 ♪</em>
        </div>
      </aside>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/characters" element={<CharactersPage />} />
          <Route path="/speakers" element={<SpeakersPage />} />
          <Route path="/emotion" element={<EmotionPage />} />
          <Route path="/synthesis" element={<SynthesisPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/devices" element={<DevicesPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}
