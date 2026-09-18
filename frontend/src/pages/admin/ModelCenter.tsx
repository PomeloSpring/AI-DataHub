import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Cpu, Terminal } from 'lucide-react';
import Admin from '../Admin';
import ExecutionLayers from './ExecutionLayers';

export default function ModelCenter() {
  return (
    <div className="h-full overflow-auto">
      <Tabs defaultValue="model-config" className="h-full">
        <div className="mb-6">
          <h1 className="text-2xl font-bold mb-4">模型中心</h1>
          <TabsList>
            <TabsTrigger value="model-config">
              <Cpu className="h-4 w-4 mr-2" />
              模型配置
            </TabsTrigger>
            <TabsTrigger value="execution-layers">
              <Terminal className="h-4 w-4 mr-2" />
              执行层
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="model-config" className="mt-0">
          <Admin embeddedTab="model-config" />
        </TabsContent>
        <TabsContent value="execution-layers" className="mt-0">
          <ExecutionLayers />
        </TabsContent>
      </Tabs>
    </div>
  );
}
