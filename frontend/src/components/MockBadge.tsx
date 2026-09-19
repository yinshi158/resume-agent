import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

/**
 * mock 状态徽标（spec §4：界面必须标示 mock）。
 * 无 key 时全链路以规则抽取运行——解析质量不代表真实水平，演示不得冒充。
 */
export default function MockBadge() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.getSettings, staleTime: 30_000 });

  if (!settings.data) return null;
  if (!settings.data.mock) {
    return <span className="mock-badge mock-live" title="已配置 API key，正在使用真实模型解析">真实模型</span>;
  }
  return (
    <span
      className="mock-badge mock-on"
      title="未配置 API key：当前为演示模式（规则抽取），解析质量不代表真实水平"
    >
      mock 演示模式
    </span>
  );
}
