import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Cpu, Workflow, MessageSquare, FlaskConical, Brain,
} from 'lucide-react';
import Admin from '../Admin';
import ModelLab from '../ModelLab';
import ModelTrain from '../ModelTrain';
import WorkflowConfig from './WorkflowConfig';
import PromptManager from './PromptManager';

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
            <TabsTrigger value="workflow">
              <Workflow className="h-4 w-4 mr-2" />
              查询模式
            </TabsTrigger>
            <TabsTrigger value="prompts">
              <MessageSquare className="h-4 w-4 mr-2" />
              Prompt管理
            </TabsTrigger>
            <TabsTrigger value="model-lab">
              <FlaskConical className="h-4 w-4 mr-2" />
              模型 Lab
            </TabsTrigger>
            <TabsTrigger value="model-train">
              <Brain className="h-4 w-4 mr-2" />
              模型微调
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="model-config" className="mt-0">
          <Admin embeddedTab="model-config" />
        </TabsContent>
        <TabsContent value="workflow" className="mt-0">
          <div className="h-[calc(100vh-220px)]">
            <WorkflowConfig />
          </div>
        </TabsContent>
        <TabsContent value="prompts" className="mt-0">
          <PromptManager />
        </TabsContent>
        <TabsContent value="model-lab" className="mt-0">
          <ModelLab />
        </TabsContent>
        <TabsContent value="model-train" className="mt-0">
          <ModelTrain />
        </TabsContent>
      </Tabs>
    </div>
  );
}
