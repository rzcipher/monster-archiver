import { Router } from "express";
import { upload } from "../lib/serverConfig";

const router = Router();

// Upload File
router.post("/api/upload", upload.single("audio"), (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: "No file uploaded" });
  }
  res.json({
    message: "File uploaded successfully",
    originalName: req.file.originalname,
    filename: req.file.filename,
    path: req.file.path,
    size: req.file.size
  });
});

export default router;
